#![cfg(feature = "http")]

use std::{
    env, fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    time::Duration,
};

use _zulip_backend::http::alert_words::remove;
use serde_json::json;
use tokio_postgres::NoTls;

const INVALIDATED_KEYS: [&str; 4] = [
    "django:1:current:realm_alert_words:42",
    "django:1:previous:realm_alert_words:42",
    "django:1:current:realm_alert_words_automaton:42",
    "django:1:previous:realm_alert_words_automaton:42",
];
const OTHER_REALM_KEY: &str = "django:1:current:realm_alert_words:43";

fn cache_command(address: SocketAddr, command: &str) -> String {
    let timeout = Duration::from_secs(2);
    let mut socket = TcpStream::connect_timeout(&address, timeout).unwrap();
    socket.set_read_timeout(Some(timeout)).unwrap();
    socket.set_write_timeout(Some(timeout)).unwrap();
    write!(socket, "{command}quit\r\n").unwrap();
    let mut reply = String::new();
    socket.read_to_string(&mut reply).unwrap();
    reply
}

#[tokio::test]
#[ignore = "requires a disposable PostgreSQL database and Memcached instance"]
async fn deletes_only_users_words_and_rolls_back_on_cache_failure() {
    let dsn = env::var("ZULIP_RUST_ALERT_WORD_TEST_DATABASE").unwrap();
    let (mut client, connection) = tokio_postgres::connect(&dsn, NoTls).await.unwrap();
    tokio::spawn(async move { connection.await.unwrap() });
    let (observer, connection) = tokio_postgres::connect(&dsn, NoTls).await.unwrap();
    tokio::spawn(async move { connection.await.unwrap() });
    client
        .batch_execute(
            "CREATE SCHEMA zulip;
             CREATE TABLE zulip.zerver_alertword (user_profile_id integer, word text);
             INSERT INTO zulip.zerver_alertword VALUES
                 (1, 'Hello'), (1, 'ος'), (1, 'keep'), (2, 'Hello');",
        )
        .await
        .unwrap();

    let fixtures = PathBuf::from(env::var_os("ZULIP_RUST_ALERT_WORD_TEST_DIR").unwrap());
    let address: SocketAddr = env::var("ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS")
        .unwrap()
        .parse()
        .unwrap();
    assert!(address.ip().is_loopback());
    let manifest = fixtures.join("alert-word-cache.json");
    let write_manifest = |address: SocketAddr| {
        let contents = json!({
            "address": address.to_string(),
            "key_prefixes": ["django:1:current:", "django:1:previous:"],
        });
        fs::write(&manifest, contents.to_string()).unwrap();
    };
    let words = ["hello".to_owned(), "ος".to_owned(), "absent".to_owned()];
    write_manifest("127.0.0.1:0".parse().unwrap());
    let error = remove(&mut client, &manifest, 1, 42, &words)
        .await
        .unwrap_err();
    assert_eq!(
        error.downcast::<std::io::Error>().unwrap().kind(),
        std::io::ErrorKind::ConnectionRefused,
    );
    let committed_words = || async {
        observer
            .query(
                "SELECT user_profile_id, word FROM zulip.zerver_alertword
                 ORDER BY user_profile_id, word COLLATE \"C\"",
                &[],
            )
            .await
            .unwrap()
            .iter()
            .map(|row| (row.get::<_, i32>(0), row.get::<_, String>(1)))
            .collect::<Vec<_>>()
    };
    let pair = |id, word: &str| (id, word.to_owned());
    assert_eq!(
        committed_words().await,
        [
            pair(1, "Hello"),
            pair(1, "keep"),
            pair(1, "ος"),
            pair(2, "Hello")
        ],
    );

    write_manifest(address);
    for key in INVALIDATED_KEYS.into_iter().chain([OTHER_REALM_KEY]) {
        assert_eq!(
            cache_command(address, &format!("set {key} 0 0 1\r\nx\r\n")),
            "STORED\r\n"
        );
    }
    assert_eq!(
        remove(&mut client, &manifest, 1, 42, &words).await.unwrap(),
        ["keep"]
    );
    assert_eq!(committed_words().await, [pair(1, "keep"), pair(2, "Hello")]);
    let keys = INVALIDATED_KEYS.join(" ");
    assert_eq!(
        cache_command(address, &format!("get {keys}\r\n")),
        "END\r\n"
    );
    assert_eq!(
        cache_command(address, &format!("get {OTHER_REALM_KEY}\r\n")),
        format!("VALUE {OTHER_REALM_KEY} 0 1\r\nx\r\nEND\r\n"),
    );

    let key = INVALIDATED_KEYS[0];
    assert_eq!(
        cache_command(address, &format!("set {key} 0 0 1\r\nx\r\n")),
        "STORED\r\n"
    );
    assert_eq!(
        remove(&mut client, &manifest, 1, 42, &[]).await.unwrap(),
        ["keep"]
    );
    assert_eq!(
        cache_command(address, &format!("get {keys}\r\n")),
        "END\r\n"
    );
    assert_eq!(committed_words().await, [pair(1, "keep"), pair(2, "Hello")]);
}
