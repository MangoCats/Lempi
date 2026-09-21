//! The Program Director: what plays next, and why `[SPEC009]`.
//!
//! Four stages, kept in separate modules because they answer different
//! questions and must not be allowed to blur into each other `[SPEC-DIR-100]`:
//!
//! ```text
//!   A. frequency ... how often may this play?   (rotation, recovery, history)
//!   B. shaping ..... does this fit right now?   (seeds, Taste, flavor)
//!   C. flow ........ does it follow what is queued?
//!   D. roulette .... weighted random over the shaped pool
//! ```
//!
//! All four stages are implemented and wired: `decide` weighs the pool
//! (A), shapes it against seeds and Taste (B), orders the survivors by flow
//! distance from the queue tail (C), then applies rank decay and spins the
//! roulette (D) `[SPEC-DIR-165]`.

pub mod flavor;
pub mod frequency;
pub mod library;
pub mod occasion;
pub mod program;
pub mod shape;
