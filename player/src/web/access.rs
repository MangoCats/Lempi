//! Who may use the web server `[REQ-AND-160]`.
//!
//! On an appliance the web UI is meant to be reached from the LAN, and asks for
//! nothing. On a phone the same server backs the app's own WebView
//! `[GDE-AND-060]` -- and any app on a phone can open a connection to a
//! localhost port, a boundary an appliance never had `[GDE-APP-090]`. So a
//! host that wants it supplies a secret, made anew at each launch, and every
//! request must carry it; one without it is refused.
//!
//! The secret can arrive three ways, because three kinds of request need it:
//!
//! - a `key=` query parameter, on the WebView's first page load -- the one
//!   request the host fully controls. The reply sets a cookie holding it;
//! - that cookie, which the page's own subresource fetches and its WebSocket
//!   handshake carry without being told to (`HttpOnly`, `SameSite=Strict`, so
//!   the page's scripts cannot read it and no other origin sends it);
//! - an `x-lempi-key` header, for a native client or a test.
//!
//! The host makes the secret -- on Android, `SecureRandom` -- so this crate
//! needs no random source; it only refuses one too short to be a secret.
//! It is never written to the log.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Router;

pub const COOKIE: &str = "lempi_key";
pub const HEADER: &str = "x-lempi-key";
pub const QUERY: &str = "key";
/// Shorter than this is not a secret; a host passing one is refused at start.
pub const MIN_SECRET_LEN: usize = 16;

/// How a request proved it may be served, or that it did not.
#[derive(Debug, PartialEq, Eq)]
pub enum Grant {
    Header,
    Cookie,
    /// By the query parameter -- answered with the cookie, so what follows
    /// needs nothing more.
    Query,
    Denied,
}

/// Equal without an early exit, so the time taken says nothing about how many
/// leading characters were right. A length mismatch returns at once; the
/// length of a secret the host chose is not what this protects.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Whether this request carries `secret`, and by which route.
pub fn granted(secret: &str, headers: &HeaderMap, query: Option<&str>) -> Grant {
    let s = secret.as_bytes();
    if headers.get(HEADER).is_some_and(|v| same(v.as_bytes(), s)) {
        return Grant::Header;
    }
    let by_cookie = headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(';'))
        .filter_map(|kv| kv.trim().split_once('='))
        .any(|(k, v)| k == COOKIE && same(v.as_bytes(), s));
    if by_cookie {
        return Grant::Cookie;
    }
    let by_query = query
        .into_iter()
        .flat_map(|q| q.split('&'))
        .filter_map(|kv| kv.split_once('='))
        .any(|(k, v)| k == QUERY && same(v.as_bytes(), s));
    if by_query {
        return Grant::Query;
    }
    Grant::Denied
}

async fn require(State(secret): State<Arc<str>>, req: Request, next: Next) -> Response {
    match granted(&secret, req.headers(), req.uri().query()) {
        Grant::Denied => (StatusCode::FORBIDDEN, "this server needs the app's launch key").into_response(),
        Grant::Query => {
            let mut resp = next.run(req).await;
            let cookie = format!("{COOKIE}={secret}; Path=/; HttpOnly; SameSite=Strict");
            if let Ok(v) = HeaderValue::from_str(&cookie) {
                resp.headers_mut().append(header::SET_COOKIE, v);
            }
            resp
        }
        Grant::Header | Grant::Cookie => next.run(req).await,
    }
}

/// The router, behind the secret if there is one. `None` leaves it as it is:
/// the appliance's LAN UI.
pub fn guard(router: Router, secret: Option<&str>) -> Router {
    match secret {
        None => router,
        Some(s) => router.layer(axum::middleware::from_fn_with_state(Arc::<str>::from(s), require)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const S: &str = "0123456789abcdef0123";

    fn headers(pairs: &[(&'static str, &str)]) -> HeaderMap {
        let mut h = HeaderMap::new();
        for (k, v) in pairs {
            h.append(*k, HeaderValue::from_str(v).unwrap());
        }
        h
    }

    #[test]
    fn each_route_grants_and_a_wrong_key_does_not() {
        assert_eq!(granted(S, &headers(&[(HEADER, S)]), None), Grant::Header);
        assert_eq!(granted(S, &headers(&[("cookie", &format!("a=b; {COOKIE}={S}"))]), None), Grant::Cookie);
        assert_eq!(granted(S, &HeaderMap::new(), Some(&format!("x=1&{QUERY}={S}"))), Grant::Query);
        assert_eq!(granted(S, &HeaderMap::new(), None), Grant::Denied, "nothing carried");
        assert_eq!(granted(S, &headers(&[(HEADER, "0123456789abcdef0124")]), None), Grant::Denied);
        assert_eq!(granted(S, &headers(&[(HEADER, "0123")]), None), Grant::Denied, "a prefix is not the key");
        assert_eq!(granted(S, &headers(&[("cookie", &format!("other={S}"))]), None), Grant::Denied,
                   "the right value under another name");
    }
}
