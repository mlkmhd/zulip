use std::{
    io::{self, BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    time::Duration,
};

const TIMEOUT: Duration = Duration::from_secs(2);
const MAX_KEY_BYTES: usize = 250;
#[cfg(feature = "python")]
const MAX_REPLY_BYTES: u64 = b"NOT_FOUND\r\n".len() as u64;

fn validate_address(address: SocketAddr) -> io::Result<()> {
    if !address.ip().is_loopback() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Memcached address must be on loopback",
        ));
    }
    Ok(())
}

pub(crate) fn validate_key(key: &str) -> io::Result<()> {
    if key.is_empty()
        || key.len() > MAX_KEY_BYTES
        || !key.bytes().all(|byte| byte.is_ascii_graphic())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Invalid Memcached key",
        ));
    }
    Ok(())
}

fn connect(address: SocketAddr) -> io::Result<TcpStream> {
    let stream = TcpStream::connect_timeout(&address, TIMEOUT)?;
    stream.set_read_timeout(Some(TIMEOUT))?;
    stream.set_write_timeout(Some(TIMEOUT))?;
    Ok(stream)
}

#[cfg(feature = "http")]
pub(crate) fn ping(address: SocketAddr) -> io::Result<bool> {
    validate_address(address)?;
    let mut stream = connect(address)?;
    stream.write_all(b"version\r\n")?;
    let mut reply = Vec::new();
    BufReader::new(stream)
        .take(128)
        .read_until(b'\n', &mut reply)?;
    Ok(reply.starts_with(b"VERSION ") && reply.ends_with(b"\r\n"))
}

#[cfg(feature = "python")]
pub(crate) fn delete_keys(address: SocketAddr, keys: &[String]) -> io::Result<()> {
    validate_address(address)?;
    if keys.is_empty() {
        return Ok(());
    }

    let mut commands = Vec::new();
    for key in keys {
        validate_key(key)?;
        write!(commands, "delete {key}\r\n")?;
    }

    let mut stream = connect(address)?;
    stream.write_all(&commands)?;
    let mut reader = BufReader::new(stream);
    let mut reply = Vec::new();
    for _ in keys {
        reply.clear();
        reader
            .by_ref()
            .take(MAX_REPLY_BYTES)
            .read_until(b'\n', &mut reply)?;
        if !matches!(reply.as_slice(), b"DELETED\r\n" | b"NOT_FOUND\r\n") {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Memcached did not acknowledge the delete",
            ));
        }
    }
    Ok(())
}
