//! Take one listener-state snapshot by hand `[REQ-LIB-160]`.
//!
//! The player takes these itself, hourly. This is for the times that are not
//! the player running: before a migration, before letting a tool loose on the
//! library, or to check the thing works before trusting it to.
//!
//!     cargo run --release --example backup_now -- data/lempi_new.db

fn main() {
    let Some(db) = std::env::args().nth(1) else {
        eprintln!("usage: backup_now <library.db>");
        std::process::exit(2);
    };
    match lempi_player::backup::snapshot(std::path::Path::new(&db)) {
        Ok(p) => println!("listener state backed up to {}", p.display()),
        Err(e) => {
            eprintln!("backup failed: {e}");
            std::process::exit(1);
        }
    }
}
