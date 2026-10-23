"""Own authenticated character selection and one headless receive worker."""
from dataclasses import dataclass, replace
import logging
import queue
import threading
import time

from enb_protocol.login import Character, Credentials, GlobalLogin
from enb_protocol.session import SpaceSession
from enb_protocol.world import NavigationObject

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionView:
    status: str
    name: str | None
    sector: str | None
    objects: tuple[NavigationObject, ...]
    position: tuple[float, float, float] | None
    error: str | None
    packets: int
    frames: int
    age: float | None
    navigation: str = ''


@dataclass(frozen=True)
class NavigationCommand:
    sector: str
    target: int
    mode: str


class SessionController:
    def __init__(self, host: str, certificate_sha256: str | None = None) -> None:
        self.host = host
        self.certificate_sha256 = certificate_sha256
        self.lock = threading.Lock()
        self.operation = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.session: SpaceSession | None = None
        self.global_login: GlobalLogin | None = None
        self.characters: list[Character] = []
        self.authenticated_at = 0.0
        self.commands: queue.Queue[NavigationCommand] = queue.Queue(maxsize=1)
        self.status = 'disconnected'
        self.name: str | None = None
        self.error: str | None = None
        self.view = SessionView('disconnected', None, None, (), None, None, 0, 0, None)

    def snapshot(self) -> SessionView:
        with self.lock:
            return replace(self.view, status=self.status, name=self.name, error=self.error)

    def character_list(self) -> list[Character]:
        with self.lock:
            return list(self.characters)

    def publish(self, session: SpaceSession) -> None:
        view = SessionView(self.status, self.name, session.world.sector, tuple(session.world.navigable()),
                           session.position, self.error, session.packets, session.frames,
                           max(0, time.monotonic() - session.received_at), session.navigation_status)
        with self.lock:
            self.view = view

    def authenticate(self, credentials: Credentials) -> None:
        if not credentials.username or not credentials.password:
            raise ValueError('Enter an account name and password')
        if not self.operation.acquire(blocking=False):
            raise RuntimeError('A session operation is already running')
        try:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError('Log out of the current character before signing in again')
            self.close_authentication()
            with self.lock:
                self.status, self.error = 'authenticating', None
            connection, characters = GlobalLogin.open(self.host, credentials, self.certificate_sha256)
            with self.lock:
                self.global_login = connection
                self.characters = characters
                self.authenticated_at = time.monotonic()
                self.status = 'choose_character'
            LOG.info('Account authentication confirmed; characters=%d', len(characters))
        except (OSError, ValueError, EOFError) as error:
            with self.lock:
                self.status, self.error = 'error', str(error)
            raise
        finally:
            self.operation.release()

    def login(self, slot: int) -> None:
        if not self.operation.acquire(blocking=False):
            raise RuntimeError('A session operation is already running')
        session: SpaceSession | None = None
        try:
            with self.lock:
                connection = self.global_login
                characters = list(self.characters)
                character = next((item for item in characters if item.slot == slot), None)
            if connection is None or character is None:
                raise ValueError('Sign in and choose one of the account’s characters')
            if time.monotonic() - self.authenticated_at > 120:
                self.close_authentication()
                raise ValueError('Character selection expired; sign in again')
            self.stop_worker()
            with self.lock:
                self.global_login = None  # Ownership transfers to SpaceSession.enter.
                self.characters = []
                self.session = None
                self.name, self.status, self.error = character.name, 'logging_in', None
                self.view = SessionView('logging_in', character.name, None, (), None, None, 0, 0, None)
            LOG.info('[%s] Headless character login started', self.name)
            session = SpaceSession.enter(self.host, connection, characters, slot)
            self.publish(session)
            with self.lock:
                self.session = session
                self.status = 'connected'
                self.stop.clear()
                self.thread = threading.Thread(target=self.run, name=f'enb-session-{self.name}')
                self.thread.start()
            LOG.info('[%s] Headless login confirmed: sector=%s, navigation_objects=%d', self.name, session.world.sector, len(session.world.navigable()))
        except Exception as error:
            if session is not None:
                try:
                    session.close()
                except OSError:
                    pass
            with self.lock:
                self.status, self.error = 'error', str(error)
            LOG.error('[%s] Headless login failed: %s', self.name, error)
            raise
        finally:
            self.operation.release()

    def navigate(self, sector: str, target: int, mode: str) -> None:
        if mode not in ('warp', 'gate'):
            raise ValueError('Choose warp or gate navigation')
        with self.lock:
            if self.status != 'connected' or self.view.sector != sector:
                raise ValueError('The requested map is not the character’s live sector')
            item = next((item for item in self.view.objects if item.object_id == target), None)
            if item is None or mode == 'gate' and item.kind != 'gate':
                raise ValueError('Choose a valid target from the live map')
            try:
                self.commands.put_nowait(NavigationCommand(sector, target, mode))
            except queue.Full:
                raise RuntimeError('A navigation command is already queued') from None

    def run(self) -> None:
        session = self.session
        if session is None:
            return
        try:
            while not self.stop.is_set():
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = None
                if command is not None and not self.stop.is_set():
                    try:
                        if command.sector != session.world.sector:
                            raise ValueError('Sector changed; queued navigation was canceled')
                        session.navigate(command.target, command.mode)
                        LOG.info('[%s] Navigation submitted: mode=%s, target=%#x', self.name, command.mode, command.target)
                    except (ValueError, RuntimeError) as error:
                        session.navigation_status = str(error)
                session.poll()
                self.publish(session)
        except Exception as error:
            with self.lock:
                self.status, self.error = 'error', str(error)
            LOG.error('[%s] Headless receive stopped: %s', self.name, error)
        finally:
            try:
                session.close(logout=self.stop.is_set())
            except OSError as error:
                LOG.error('[%s] Session socket close failed: %s', self.name, error)
            with self.lock:
                if self.status == 'connected':
                    self.status = 'disconnected'
            LOG.info('[%s] Headless session stopped: packets=%d, frames=%d', self.name, session.packets, session.frames)

    def close_authentication(self) -> None:
        with self.lock:
            connection = self.global_login
            self.global_login = None
            self.characters = []
        if connection is not None:
            connection.close()

    def stop_worker(self) -> None:
        self.stop.set()
        if self.session is not None:
            self.session.cancel.set()
        thread = self.thread
        if thread is not None:
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError('Previous session worker did not stop; no replacement login sent')
            self.thread = None
        while not self.commands.empty():
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break

    def logout(self) -> None:
        if not self.operation.acquire(blocking=False):
            raise RuntimeError('A session operation is already running')
        try:
            self.stop_worker()
            self.close_authentication()
            with self.lock:
                self.status, self.error, self.name = 'disconnected', None, None
                self.view = SessionView('disconnected', None, None, (), None, None, 0, 0, None)
        finally:
            self.operation.release()
