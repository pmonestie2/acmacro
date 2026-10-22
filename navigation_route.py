"""Cooperative routes using sector-scoped captured IDs and map coordinates."""
import json
import math
from pathlib import Path
import time

from gate_navigation import GATE_IDS, submit
from focus import process_identity
from nav_catalog import CAPTURE, STORE, nearby_contacts, refresh
from objects import GATES, GATE_OBJ, STATION_OBJ, ActoolObject, Point
from protocol.decode_capture import decode_datagram
from util import log_event
from watchdog import GameUnresponsive

SECTORS = {'earth': 'Earth', 'aba': 'Asteroid Belt Alpha', 'saturn': 'Saturn',
           'glenn': 'Glenn', 'carp': 'Carpenter'}
GATE_NAMES = {('saturn','glenn'): 'Gate To Beta Hydri System',
              ('glenn','carp'): 'Sector Gate to Carpenter',
              ('earth','aba'): 'Sector Gate to Asteroid Belt Alpha',
              ('aba','saturn'): 'Sector Gate to Saturn'}
ANCHORS = {'Saturn': [("Sector Gate to Akeron's Gate", (429,568)),
                       ('Sector Gate to Asteroid Belt Alpha', (379,588))],
           'Glenn': [('Sector Gate to Carpenter', (438,613)),
                     ('Gate To Sirius System', (480,507))],
           'Carpenter': [('Gate To Tau Ceti System', (369,551)),
                         ('Sector Gate to Glenn', (275,488))]}
AVOID_STOPS = {'Carpenter': {'nav carpenter 3', 'nav orsini 2', "yamuna's weft",
                             'chavez hideout', 'lagrange 4 point', 'lagrange 5 point'},
               'Glenn': {'glenn', 'chavez resupply depot'}}
# User-confirmed yellow contacts plus the documented Saturn mobile vendor.
# This is explicit identity evidence, not an inferred packet hostility bit.
NONAGGRESSIVE_NAMES = {'net-7 field report frigate',
                      "monty's high quality manufacturing components!",
                      'interstellarms light freighter'}
DISCOVERY_ORDER = {'Glenn': ['Glenn 1','Glenn 2','Glenn 3','Glenn 4','Glenn 5','Glenn 6','Glenn 7']}
# Common STARBASE heading on the normalized station map, sampled from an
# actual station screenshot. This is independent of class/inventory icons.
STARBASE_MAP = ActoolObject('station map heading',
    [Point(x, y, 254) for x,y in [(212,511),(214,511),(208,513),(218,513),
     (208,515),(208,517),(210,517),(214,517),(216,517),(226,517),
     (240,517),(256,517),(262,517),(268,517),(278,517),(218,519),
     (238,519),(256,519),(276,519),(288,519),(300,519),(236,521),
     (242,521),(254,521),(274,521),(280,521),(294,521),(208,523),
     (216,523),(242,523),(268,523),(300,523)]], (0,0))


def find_named(objects, name):
    return next((o for o in objects if (o.get('name') or '').strip().casefold() == name.casefold()), None)


def map_point(xyz, anchors):
    """Convert physical coordinates to the controller's normalized small map."""
    if len(anchors) < 2:
        raise ValueError('Two verified map anchors are required')
    (a, sa), (b, sb) = anchors[:2]
    result = []
    for axis in (0, 1):
        if abs(b[axis] - a[axis]) < 1:
            raise ValueError('Map anchors do not span this axis')
        result.append(round(sa[axis] + (xyz[axis]-a[axis]) * (sb[axis]-sa[axis]) / (b[axis]-a[axis])))
    if not (24 <= result[0] <= 495 and 370 <= result[1] <= 625):
        raise ValueError(f'Navpoint falls outside the map: {result}')
    return tuple(result)


class CapturedNavigator:
    def __init__(self, controller):
        self.controller = controller
        self.folder = Path(__file__).resolve().parent / 'navigation'
        self.folder.mkdir(exist_ok=True)

    def world(self):
        worlds = json.loads(CAPTURE.read_text())
        instance = self.controller.instance
        pid = instance.proxy_pid
        if not instance.valid() or process_identity(pid) != instance.proxy_born:
            raise GameUnresponsive('Owned game/proxy exited before world lookup')
        status = json.loads((CAPTURE.parent/'status.json').read_text())
        active = status.get('active_proxies',{}).get(str(pid))
        if not active or time.time()-status['updated'] > 15:
            raise GameUnresponsive('Owned proxy is not actively captured')
        key = f'{pid}_{active["started"]}'
        if key not in worlds:
            raise GameUnresponsive('No captured world for the owned proxy')
        world = worlds[key]
        if time.time() - world['recent']['updated'] > 15:
            raise GameUnresponsive('World capture is stale; refusing to navigate from old contacts')
        refresh()
        return world

    def screen_point(self, sector, target):
        saved = json.loads(STORE.read_text())['sectors'].get(sector, {})
        anchors = []
        for name, screen in ANCHORS.get(sector, []):
            obj = find_named(saved.values(), name)
            if obj:
                anchors.append((obj['raw_xyz'],screen))
        point = map_point(target['raw_xyz'], anchors)
        with (self.folder/'map_calibrations.jsonl').open('a') as output:
            output.write(json.dumps({'time':time.time(),'sector':sector,'target':target['name'],
                                     'raw_xyz':target['raw_xyz'],'screen':point,'anchors':anchors})+'\n')
        return point

    def safe_stop(self, target, world):
        hazards = json.loads(STORE.read_text()).get('hazards', {}).get(world['recent']['sector'], {})
        if any(math.dist(target['raw_xyz'],zone['raw_xyz']) < 15000 for zone in hazards.values()):
            raise GameUnresponsive(f'Known V\'rix respawn hazard near {target["name"]}')
        if target['name'].strip().casefold() in AVOID_STOPS.get(world['recent']['sector'], set()):
            raise GameUnresponsive(f'Known dangerous stop: {target["name"]}')
        friendly_ids = set()
        evidence = self.folder/'contact_observations.jsonl'
        if evidence.exists():
            session = f'{self.controller.instance.proxy_pid}_{world["process"]["started"]}'
            for line in evidence.read_text().splitlines():
                item = json.loads(line)
                if (item.get('toon') == self.controller.player.toon_name and
                        item.get('proxy_session') == session and item.get('epoch') == world['epoch'] and
                        item.get('color') in {'green','yellow'}):
                    friendly_ids.add(item['id'])
        contacts = nearby_contacts(target['raw_xyz'],world['catalog'],nonaggressive_names=NONAGGRESSIVE_NAMES,
                                   nonaggressive_ids=friendly_ids)
        if contacts:
            raise GameUnresponsive('Unsafe endpoint near contacts: ' + ', '.join(o.get('name',o['id']) for o in contacts))

    def waypoint(self, target):
        world = self.world()
        self.safe_stop(target,world)
        # Intermediate routes do not need a client target/panel. This also
        # works in a newly entered sector before two map anchors are known.
        with self.controller.with_focus('intermediate navigation'):
            report = submit(self.controller.instance,int(target['id'],16))
        if report['state'] != 'handler_returned':
            raise GameUnresponsive(f'Intermediate navigation not submitted: {report}')
        log_event(f'Intermediate waypoint: {target["name"]} {target["id"]}')
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            world = self.world()
            player = world['recent'].get('player')
            if player and time.time()-player['last_seen'] < 15:
                if math.dist(player['raw_xyz'],target['raw_xyz']) < 3500:
                    # Return promptly: the caller continues discovery/navigation,
                    # rather than waiting at the intermediate endpoint.
                    return
            self.controller._yield(3 if self.controller.singleton else 8)
        raise GameUnresponsive(f'Intermediate waypoint did not arrive: {target["name"]}')

    def discover(self, name, hint, max_steps=6):
        """Approach safe, already observed navpoints when a gate is unavailable."""
        visited = set()
        for _ in range(max_steps+1):
            world = self.world()
            target = find_named(world['catalog'],name)
            if target:
                return target
            player = world['recent'].get('player')
            if not player or time.time()-player['last_seen'] > 15:
                raise GameUnresponsive('No recent player position for intermediate routing')
            candidates = []
            for obj in world['catalog']:
                if obj['id'] in visited or not obj.get('name','').lower().startswith('nav ') or 'raw_xyz' not in obj:
                    continue
                if math.dist(obj['raw_xyz'],hint) >= math.dist(player['raw_xyz'],hint)-1000:
                    continue
                try:
                    self.safe_stop(obj,world)
                except GameUnresponsive:
                    continue
                candidates.append(obj)
            if not candidates:
                raise GameUnresponsive(f'No safe intermediate nav available to discover {name}')
            intermediate = min(candidates,key=lambda obj:math.dist(obj['raw_xyz'],hint))
            visited.add(intermediate['id'])
            self.waypoint(intermediate)
        raise GameUnresponsive(f'{name} not discovered after {max_steps} intermediate navs')

    def gate(self, source, destination):
        world = self.world()
        sector = SECTORS[source]
        if world['recent']['sector'] != sector:
            raise GameUnresponsive('Captured sector does not match the route')
        name = GATE_NAMES[(source,destination)]
        target = find_named(world['catalog'],name)
        if not target:
            saved = json.loads(STORE.read_text())['sectors'].get(sector,{})
            historical = find_named(saved.values(),name)
            if not historical:
                visited = set()
                for _ in range(8):
                    world = self.world()
                    target = find_named(world['catalog'],name)
                    if target:
                        break
                    candidates = [find_named(world['catalog'],nav) for nav in DISCOVERY_ORDER.get(sector,[])]
                    candidates = [obj for obj in candidates if obj and obj['id'] not in visited]
                    chosen = None
                    for obj in candidates:
                        try:
                            self.safe_stop(obj,world)
                        except GameUnresponsive:
                            continue
                        chosen = obj
                        break
                    if not chosen:
                        raise GameUnresponsive(f'No safe discovered intermediate nav toward {name}')
                    visited.add(chosen['id'])
                    self.waypoint(chosen)
                if not target:
                    raise GameUnresponsive(f'Gate {name} remains undiscovered after intermediate navs')
            else:
                target = self.discover(name,historical['raw_xyz'])
            world = self.world()
        self.safe_stop(target,world)
        point = GATES.get(source,{}).get(destination) or self.screen_point(sector,target)
        GATES.setdefault(source,{})[destination] = point
        GATE_IDS[source,destination] = int(target['id'],16)
        log_event(f'Captured gate {source}->{destination}: {target["name"]} {target["id"]} at {point}')
        original_transition = self.controller.transition_space
        if destination == 'glenn':
            self.controller.transition_space = self.depart_glenn
        try:
            self.controller.travel_one(source,destination)
        finally:
            self.controller.transition_space = original_transition
        # Do not linger at a newly entered gate while discovering the onward
        # route. V'rix at the arrival point can incapacitate a low-level ship.
        arrival = self.world()
        player = arrival['recent'].get('player')
        if player and time.time()-player['last_seen'] < 15:
            threats = nearby_contacts(player['raw_xyz'], arrival['catalog'])
            threats = [obj for obj in threats if "v'rix" in (obj.get('name') or '').casefold()]
            hazards = json.loads(STORE.read_text()).get('hazards',{}).get(arrival['recent']['sector'],{})
            if threats or any(math.dist(player['raw_xyz'],zone['raw_xyz']) < 15000 for zone in hazards.values()):
                log_event('V\'rix at arrival gate; leaving immediately toward a safe intermediate nav')
                for name in DISCOVERY_ORDER.get(SECTORS[destination], []):
                    escape = find_named(arrival['catalog'],name)
                    if not escape:
                        continue
                    try:
                        self.safe_stop(escape, arrival)
                    except GameUnresponsive:
                        continue
                    self.waypoint(escape)
                    break
                else:
                    raise GameUnresponsive('V\'rix at arrival gate and no safe discovered escape nav')

    def depart_glenn(self, expected=None):
        """Hold focus through entry and escape; never wait for map recognition.

        The capture must prove this proxy entered Glenn and expose a live target
        ID. Submit once, then stop only after movement clears remembered mobs.
        The exclusive lock prevents the peer claiming focus during this escape.
        """
        started = time.time()
        sent = False
        origin = None
        with self.controller.exclusive_focus('Glenn gate immediate escape', timeout=45):
            while time.time()-started < 43:
                world = self.world()
                player = world['recent'].get('player')
                if world['recent']['sector'] == 'Glenn' and player and player['last_seen'] >= started:
                    if not sent:
                        target = find_named(world['catalog'],'Glenn 1')
                        if target:
                            self.safe_stop(target,world)
                            origin = player['raw_xyz']
                            report = submit(self.controller.instance,int(target['id'],16))
                            if report['state'] != 'handler_returned':
                                raise GameUnresponsive(f'Glenn escape not submitted: {report}')
                            sent = True
                            log_event('Glenn captured: immediate warp toward Glenn 1')
                    elif math.dist(origin,player['raw_xyz']) > 20000:
                        # The ship has actually moved, and this stop must also
                        # clear current contacts and persistent respawn areas.
                        try:
                            self.safe_stop({'name':'Glenn escape stop','raw_xyz':player['raw_xyz']},world)
                        except GameUnresponsive:
                            pass
                        else:
                            self.controller.key_press('q')
                            self.controller.player.set_pos('glenn')
                            log_event('Glenn escape: stopped clear of recorded mobs')
                            return True
                self.controller._yield(.2)
        raise GameUnresponsive('Glenn transition/escape did not complete within 45 seconds')

    def docking_message(self, since):
        path = CAPTURE.parent / f'packets_{self.controller.instance.proxy_pid}.jsonl'
        with path.open('rb') as stream:
            stream.seek(0,2)
            length = stream.tell()
            stream.seek(max(0,length-256000))
            lines = stream.read().splitlines()
        for line in lines:
            try:
                item = json.loads(line)
                if item['outgoing'] or item['time'] < since:
                    continue
                packet = decode_datagram(bytes.fromhex(item['payload_hex']))
                for frame in packet.get('frames',[]):
                    message = frame.get('chat',{}).get('message','').casefold()
                    if 'orsini' in message and 'landing clearance granted' in message:
                        return item['time']
            except (ValueError,KeyError):
                continue
        return None

    def dock_orsini(self):
        # Wiki coordinates are a discovery hint only; the submitted ID must
        # come from this proxy's current Carpenter epoch.
        target = self.discover('Orsini Mining Platform',[26580,89030,0])
        world = self.world()
        self.safe_stop(target,world)
        point = self.screen_point('Carpenter',target)
        with self.controller.with_focus('Orsini selection and navigation'):
            self.controller.get_map()
            self.controller.lclick(*point)
            self.controller._yield(.3)
            report = submit(self.controller.instance,int(target['id'],16))
        if report['state'] != 'handler_returned':
            raise GameUnresponsive(f'Orsini navigation not submitted: {report}')
        if not self.controller.wait_gate_objects([STATION_OBJ],timeout=240):
            raise GameUnresponsive('Orsini docking panel did not become available')
        started = time.time()
        self.controller.lclick(1236,515)
        deadline = time.monotonic()+120
        message = None
        while time.monotonic() < deadline:
            message = message or self.docking_message(started)
            self.controller.get_map()
            if message and self.controller.test_obj(STARBASE_MAP,screenshot=self.controller.screenshot()):
                evidence = {'time':time.time(),'station':target['name'],'sector':'Carpenter',
                            'target_id':target['id'],'raw_xyz':target['raw_xyz'],
                            'landing_message_time':message,'station_map_confirmed':True,
                            'proxy_pid':self.controller.instance.proxy_pid}
                (self.folder/'orsini_arrival.json').write_text(json.dumps(evidence,indent=2))
                self.controller.player.set_pos('orsini')
                return 'orsini'
            self.controller._yield(8)
        raise GameUnresponsive('Orsini loading screen/docking did not finish within two minutes')

    def route(self, source, destination):
        self.controller.player.current_dest = destination
        # Known UI routes retain their existing behavior. New gates use the
        # captured sector identity, destination ID and normalized map anchors.
        if destination not in {'carp','orsini'}:
            return self.controller.travel_all(source,destination)
        if source in {'earth','aba'}:
            if source == 'earth':
                self.gate('earth','aba')
            self.gate('aba','saturn')
            source = 'saturn'
        if source == 'saturn':
            self.gate('saturn','glenn')
            source = 'glenn'
        if source == 'glenn':
            self.gate('glenn','carp')
            source = 'carp'
        if source != 'carp':
            raise ValueError(f'No Carpenter route from {source}')
        if destination == 'orsini':
            return self.dock_orsini()
        return source
