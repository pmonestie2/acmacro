//! Socket ownership and dispatch. Arc shares the server between threads; Mutex
//! permits exactly one handler to modify the game at a time. Socket reads and
//! writes occur outside that lock, so a slow client cannot freeze the sector.
use crate::content::Sector;
use crate::crypto::EncryptedConnection;
use crate::game::GameState;
use crate::packets;
use crate::protocol::SECTOR_PORT;
use crate::protocol::{invalid, Datagram, Frame, Reader};
use std::collections::HashMap;
use std::io;
use std::net::{Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

pub struct Config {
    pub bind: Ipv4Addr,
    pub sector: Sector,
    pub state_dir: PathBuf,
}

#[derive(Default)]
struct ConnectionState {
    authenticated: Option<u32>,
    player: Option<u32>,
}

fn lock_game(game: &Mutex<GameState>) -> io::Result<std::sync::MutexGuard<'_, GameState>> {
    game.lock()
        .map_err(|_| io::Error::other("game state lock poisoned"))
}

/// PBKDF2 runs outside the game mutex so a login cannot pause active ships.
fn authenticate_proxy(body: &[u8], peer: SocketAddr, game: &Mutex<GameState>) -> io::Result<Frame> {
    let mut reader = Reader::new(body);
    let username = reader.string()?;
    let password = reader.string()?;
    let account = lock_game(game)?.authentication_account(&username);
    let ticket = match account {
        Ok(account) if account.verify(&username, &password) => {
            lock_game(game)?.issue_verified_ticket(account.character.game_id, peer.ip())
        }
        _ => Err(invalid("invalid local sandbox credentials")),
    };
    let response = match ticket {
        Ok(ticket) => format!("Valid=TRUE\r\nTicket={ticket}\r\n").into_bytes(),
        Err(error) => {
            eprintln!("Local account authentication rejected ({peer}): {error}");
            b"Valid=False\r\n".to_vec()
        }
    };
    Ok(Frame::new(0x2001, response))
}

fn dispatch(
    frame: Frame,
    peer: SocketAddr,
    bridge: bool,
    context: &mut ConnectionState,
    game: &Mutex<GameState>,
) -> io::Result<Vec<Frame>> {
    if bridge && frame.opcode == 0x2000 {
        return Ok(vec![authenticate_proxy(&frame.body, peer, game)?]);
    }
    let mut state = lock_game(game)?;
    let mut reader = Reader::new(&frame.body);
    let response = match frame.opcode {
        0 => {
            let major = reader.be_u32()?;
            let minor = reader.be_u32()?;
            Some(Frame::new(
                1,
                (if major == 42 && minor == 0 { 0u32 } else { 1 })
                    .to_le_bytes()
                    .to_vec(),
            ))
        }
        0x2010 | 0x2011 if bridge => None,
        0x3002 if bridge => {
            let id = reader.u32()?;
            if id == 0 {
                context.player = None;
                context.authenticated = None;
            } else if state.has_player(id) && state.selected_from(id, peer.ip()) {
                context.player = Some(id);
            } else {
                return Err(invalid("unknown proxy player identity"));
            }
            None
        }
        0x6d | 0x2002 => {
            let ticket = if frame.opcode == 0x6d {
                let length = reader.u32()? as usize;
                if length == 0 || length > 512 {
                    return Err(invalid("invalid global ticket length"));
                }
                String::from_utf8(reader.take(length)?.to_vec())
                    .map_err(|_| invalid("invalid ticket text"))?
            } else {
                String::from_utf8(frame.body).map_err(|_| invalid("invalid proxy ticket text"))?
            };
            if let Some(id) = state.consume_ticket(ticket.trim_end_matches('\0'), peer.ip()) {
                context.authenticated = Some(id);
                println!("Local account ticket accepted ({peer})");
                Some(state.characters(id)?)
            } else {
                Some(packets::global_error(
                    "Invalid or expired sandbox ticket",
                    14,
                ))
            }
        }
        0x6e | 0x2004 => {
            if let Some(id) = context.authenticated {
                let slot = reader.be_u32()?;
                match state.select(id, slot, peer.ip(), bridge) {
                    Ok(reply) => {
                        context.player = Some(id);
                        Some(reply)
                    }
                    Err(error) => Some(packets::global_error(&error.to_string(), 18)),
                }
            } else {
                Some(packets::global_error(
                    "Authenticate before selecting a character",
                    14,
                ))
            }
        }
        _ if bridge && context.player.is_some() => {
            state.handle_input(context.player.unwrap(), frame.opcode, &frame.body)?;
            None
        }
        _ => {
            eprintln!(
                "Unsupported {} TCP opcode {:#06x}, bytes={}",
                if bridge { "bridge" } else { "global" },
                frame.opcode,
                frame.body.len()
            );
            None
        }
    };
    Ok(response.into_iter().collect())
}

fn serve_connection(
    mut stream: TcpStream,
    peer: SocketAddr,
    bridge: bool,
    game: Arc<Mutex<GameState>>,
) -> io::Result<()> {
    // Each connection worker uses blocking reads with deadlines. Explicitly
    // set the accepted stream's mode: inheritance differs across operating
    // systems, and read_exact must be able to wait for the next TCP fragment.
    stream.set_nonblocking(false)?;
    stream.set_nodelay(true)?;
    stream.set_read_timeout(Some(Duration::from_secs(120)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let mut context = ConnectionState::default();
    if bridge {
        loop {
            let frame = Frame::read(&mut stream)?;
            for reply in dispatch(frame, peer, true, &mut context, &game)? {
                reply.write(&mut stream)?;
            }
        }
    } else {
        stream.set_read_timeout(Some(Duration::from_secs(10)))?;
        let mut connection = EncryptedConnection::accept(stream)?;
        connection
            .stream
            .set_read_timeout(Some(Duration::from_secs(120)))?;
        loop {
            let frame = connection.read()?;
            for reply in dispatch(frame, peer, false, &mut context, &game)? {
                connection.write(&reply)?;
            }
        }
    }
}

type Connections = Arc<Mutex<HashMap<u64, TcpStream>>>;

fn accept_connections(
    listener: TcpListener,
    bridge: bool,
    game: Arc<Mutex<GameState>>,
    stop: Arc<AtomicBool>,
    connections: Connections,
    ids: Arc<AtomicU64>,
) {
    let mut workers: Vec<JoinHandle<()>> = Vec::new();
    while !stop.load(Ordering::Relaxed) {
        workers.retain(|worker| !worker.is_finished());
        match listener.accept() {
            Ok((stream, peer)) => {
                let id = ids.fetch_add(1, Ordering::Relaxed);
                let Ok(mut registry) = connections.lock() else {
                    break;
                };
                // Check under the registry lock too: shutdown may have closed
                // the existing sockets after accept() returned this new one.
                if stop.load(Ordering::Relaxed) {
                    let _ = stream.shutdown(Shutdown::Both);
                    break;
                }
                if registry.len() >= 128 {
                    let _ = stream.shutdown(Shutdown::Both);
                    continue;
                }
                let clone = match stream.try_clone() {
                    Ok(clone) => clone,
                    Err(error) => {
                        eprintln!("TCP clone failed: {error}");
                        continue;
                    }
                };
                registry.insert(id, clone);
                drop(registry);
                let shared = Arc::clone(&game);
                let owned = Arc::clone(&connections);
                workers.push(thread::spawn(move || {
                    if let Err(error) = serve_connection(stream, peer, bridge, shared) {
                        if !matches!(
                            error.kind(),
                            io::ErrorKind::UnexpectedEof
                                | io::ErrorKind::ConnectionReset
                                | io::ErrorKind::ConnectionAborted
                        ) {
                            eprintln!("TCP {peer} ended: {error}");
                        }
                    }
                    if let Ok(mut registry) = owned.lock() {
                        registry.remove(&id);
                    }
                }));
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(25))
            }
            Err(error) => {
                eprintln!("TCP accept failed: {error}");
                break;
            }
        }
    }
    // Main closes all registered sockets before joining acceptor threads.
    for worker in workers {
        let _ = worker.join();
    }
}

pub fn run(config: Config, stop: Arc<AtomicBool>) -> io::Result<()> {
    let stop_request = config.state_dir.join("stop.request");
    let status_path = config.state_dir.join("server.local.json");
    std::fs::create_dir_all(&config.state_dir)?;
    // A request belongs to one process lifetime; a previous stop must not stop
    // the replacement immediately.
    if stop_request.exists() {
        std::fs::remove_file(&stop_request)?;
    }
    // Bind every endpoint before starting threads: partial startup never leaves
    // some server ports running after another port reports an error.
    let mut tcp = Vec::new();
    // Installed Net7Proxy 2.46 opens its account bridge on TCP 3807, while
    // the archived proxy opens it on 3809. UDP 3807 is a separate socket.
    // Keep both bridge endpoints for compatibility with either proxy version.
    for (port, bridge) in [(3805, false), (3807, true), (3809, true), (3810, true)] {
        let listener = TcpListener::bind((config.bind, port)).map_err(|e| {
            io::Error::new(
                e.kind(),
                format!("cannot bind TCP {}:{port}: {e}", config.bind),
            )
        })?;
        listener.set_nonblocking(true)?;
        tcp.push((listener, bridge));
    }
    let mut udp = HashMap::new();
    for port in [3806, 3807, 3808, SECTOR_PORT] {
        let socket = UdpSocket::bind((config.bind, port)).map_err(|e| {
            io::Error::new(
                e.kind(),
                format!("cannot bind UDP {}:{port}: {e}", config.bind),
            )
        })?;
        socket.set_nonblocking(true)?;
        udp.insert(port, socket);
    }
    let game = Arc::new(Mutex::new(GameState::new(
        config.sector,
        config.bind,
        config.state_dir,
    )?));
    std::fs::write(
        status_path,
        serde_json::to_vec_pretty(&serde_json::json!({
            "pid": std::process::id(),
            "started_at": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs_f64(),
            "bind": config.bind.to_string(),
            "command": std::env::args().collect::<Vec<_>>(),
        }))?,
    )?;
    let connections: Connections = Arc::new(Mutex::new(HashMap::new()));
    let ids = Arc::new(AtomicU64::new(1));
    let mut acceptors = Vec::new();
    for (listener, bridge) in tcp {
        let game = Arc::clone(&game);
        let stop = Arc::clone(&stop);
        let connections = Arc::clone(&connections);
        let ids = Arc::clone(&ids);
        acceptors.push(thread::spawn(move || {
            accept_connections(listener, bridge, game, stop, connections, ids)
        }));
    }
    println!(
        "Rust ENB sandbox ready: {} (shared sectors and player sessions)",
        config.bind
    );
    {
        let state = lock_game(&game)?;
        println!("Local sandbox accounts loaded: {}", state.account_count());
    }
    println!("TCP 3805 global, 3807/3809/3810 proxy bridge; UDP 3806 registration, 3808 master, {SECTOR_PORT} sector");
    let mut buffer = [0u8; 65535];
    let mut last_tick = Instant::now();
    let mut failure = None;
    while !stop.load(Ordering::Relaxed) {
        if stop_request.exists() {
            break;
        }
        for (port, socket) in &udp {
            for _ in 0..128 {
                match socket.recv_from(&mut buffer) {
                    Ok((size, peer)) => {
                        let result = Datagram::decode(&buffer[..size])
                            .and_then(|packet| lock_game(&game)?.handle_udp(*port, peer, packet));
                        if let Err(error) = result {
                            eprintln!("Rejected UDP {peer}, port={port}, size={size}: {error}");
                            // Header metadata helps diagnose proxy-version differences
                            // without recording login tickets or message contents.
                            if size >= 12 {
                                eprintln!(
                                    "UDP header: claimed_size={}, opcode=0x{:04x}, sequence={}",
                                    u16::from_le_bytes([buffer[0], buffer[1]]),
                                    u16::from_le_bytes([buffer[2], buffer[3]]),
                                    u32::from_le_bytes(buffer[8..12].try_into().unwrap())
                                );
                            }
                        }
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                    Err(error) => {
                        eprintln!("UDP read error on {port}: {error}");
                        break;
                    }
                }
            }
        }
        let now = Instant::now();
        let dt = (now - last_tick).as_secs_f32().min(0.25);
        last_tick = now;
        let output = match lock_game(&game).and_then(|mut state| {
            state.advance(dt)?;
            Ok(state.take_outgoing())
        }) {
            Ok(output) => output,
            Err(error) => {
                failure = Some(error);
                break;
            }
        };
        for packet in output {
            if let Some(socket) = udp.get(&packet.port) {
                if let Err(error) = socket.send_to(&packet.data, packet.peer) {
                    eprintln!("UDP send error: {error}");
                }
            }
        }
        thread::sleep(Duration::from_millis(50));
    }
    stop.store(true, Ordering::Relaxed);
    if let Ok(registry) = connections.lock() {
        for stream in registry.values() {
            let _ = stream.shutdown(Shutdown::Both);
        }
    }
    for acceptor in acceptors {
        let _ = acceptor.join();
    }
    lock_game(&game)?.save()?;
    println!("Server stopped; character position saved");
    match failure {
        Some(error) => Err(error),
        None => Ok(()),
    }
}
