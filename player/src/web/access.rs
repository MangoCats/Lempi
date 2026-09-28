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

// --- Cross-site guard `[SecurityReview C2]` ---------------------------------
//
// The appliance UI is meant to be open on the LAN and asks for nothing (a
// phone adds the launch key above). Being open to the LAN is a choice; being
// driven by a page the household merely *visited* is not. A bodyless or
// `text/plain` POST crosses origins with no CORS preflight, so without this a
// visited page could POST `/power/off`, `/wifi/forget/*`, `/mesh/*` or
// `/discovery/announce/*`, and a DNS-rebinding page could read the snapshot.
//
// Two checks, the pair the review names, because each covers what the other
// misses:
//   * **Origin equals Host.** A browser sets `Origin` on every state-changing
//     request; a cross-site one carries a foreign authority. Comparing it to
//     `Host` needs no address list and works whether the node is reached by IP
//     or by name. A GET navigation sends no `Origin`, which is allowed.
//   * **Host is one we answer to.** DNS rebinding defeats the first check --
//     the page's own foreign name resolves here, so `Origin` and `Host` agree
//     on that name. But that name is not `localhost`, this node's hostname, or
//     a bare IP literal, so it is refused here.

/// The `Host` authorities this node legitimately answers to. A bare IP literal
/// is allowed dynamically (an appliance is usually reached by address); a
/// *name* must be one of these, so an attacker's rebinding domain is not.
fn local_host_names() -> Arc<std::collections::HashSet<String>> {
    let mut s = std::collections::HashSet::new();
    for n in ["localhost", "127.0.0.1", "::1", "lempi", "lempi.lan"] {
        s.insert(n.to_string());
    }
    let h = crate::discovery::host_name().to_ascii_lowercase();
    if h != "unknown" && !h.is_empty() {
        s.insert(h.clone());
        s.insert(format!("{h}.local"));
    }
    Arc::new(s)
}

/// The host part of an authority (`host:port`, or `[v6]:port`), lower-cased.
fn host_of(authority: &str) -> String {
    let host = if let Some(rest) = authority.strip_prefix('[') {
        rest.split_once(']').map(|(h, _)| h).unwrap_or(rest)
    } else {
        authority.rsplit_once(':').map(|(h, _)| h).unwrap_or(authority)
    };
    host.to_ascii_lowercase()
}

fn host_ok(names: &std::collections::HashSet<String>, authority: &str) -> bool {
    let host = host_of(authority);
    names.contains(&host) || host.parse::<std::net::IpAddr>().is_ok()
}

async fn same_origin(
    State(names): State<Arc<std::collections::HashSet<String>>>,
    req: Request,
    next: Next,
) -> Response {
    let headers = req.headers();
    let host = headers.get(header::HOST).and_then(|v| v.to_str().ok()).unwrap_or("");
    if !host_ok(&names, host) {
        return (StatusCode::FORBIDDEN, "unrecognised Host").into_response();
    }
    if let Some(origin) = headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) {
        // `Origin: null` (a sandboxed frame, `file://`) has no authority and
        // never equals `Host`, so it is refused, which is what we want.
        let authority = origin.split_once("://").map(|(_, a)| a).unwrap_or(origin);
        if !authority.eq_ignore_ascii_case(host) {
            return (StatusCode::FORBIDDEN, "cross-site request refused").into_response();
        }
    }
    next.run(req).await
}

/// Wrap a router so a cross-site or rebinding request is refused before any
/// handler (including the `/ws` upgrade) runs. Applied on every build; the
/// launch-key `guard` above is the phone's extra layer, this is everyone's.
pub fn origin_guard(router: Router) -> Router {
    router.layer(axum::middleware::from_fn_with_state(local_host_names(), same_origin))
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
