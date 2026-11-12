//! The CEO app: one window over the board's status files. Its one command, `feed`, hands the page the board
//! folder as it is on disk; the page's `model.js` reads it, and nothing is written anywhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod feed;

/// The board folder's status files as one JSON string, read off the main thread.
#[tauri::command]
async fn feed() -> String {
    feed::feed(&feed::board_dir())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![feed])
        .run(tauri::generate_context!())
        .expect("the CEO app could not start");
}
