//! The board folder as it is on disk: each status file's name, modification time and text, as one JSON string.
//! The standard library alone, so `rustc` compiles it without Cargo or Tauri. Nothing is classified here: the
//! page's `model.js` holds every rule.
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// `$CEO_HOME/board`, else `$HOME/.ceo/board`: the folder `tools/fleet.py pull` keeps, read as the kernel's
/// `ceo_base.home()` reads `CEO_HOME` (empty counts as unset; `~` is the home folder, `~name` that user's).
pub fn board_dir() -> PathBuf {
    board_dir_of(std::env::var_os("CEO_HOME"), std::env::var_os("HOME"), user_home)
}

fn board_dir_of(ceo_home: Option<OsString>, home: Option<OsString>, lookup: impl Fn(&str) -> Option<PathBuf>) -> PathBuf {
    let home = home.filter(|one| !one.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("~"));
    let base = match ceo_home.filter(|one| !one.is_empty()).map(PathBuf::from) {
        Some(set) => expand(&set, &home, lookup).unwrap_or(set),
        None => home.join(".ceo"),
    };
    base.join("board")
}

/// path with its leading `~` made the home folder and its `~name` that user's, as Python's `expanduser` does; None
/// when path starts with neither or name is no user, and the path then stays as it is.
fn expand(path: &Path, home: &Path, lookup: impl Fn(&str) -> Option<PathBuf>) -> Option<PathBuf> {
    let mut parts = path.components();
    let name = parts.next()?.as_os_str().to_str()?.strip_prefix('~')?;
    let base = if name.is_empty() { home.to_path_buf() } else { lookup(name)? };
    Some(base.join(parts.as_path()))
}

/// name's home folder from the system's user database (`getpwnam_r`, which std links already); None for no user.
#[cfg(any(target_os = "linux", target_os = "macos"))]
fn user_home(name: &str) -> Option<PathBuf> {
    use std::{ffi::{c_char, c_int, CStr, CString, OsStr}, mem::MaybeUninit, os::unix::ffi::OsStrExt, ptr};
    #[repr(C)] #[allow(dead_code)]
    struct Passwd { // <pwd.h>'s struct passwd in full, as glibc, musl and macOS lay it out
        name: *const c_char, password: *const c_char, uid: u32, gid: u32,
        #[cfg(target_os = "macos")] change: i64, #[cfg(target_os = "macos")] class: *const c_char,
        gecos: *const c_char, dir: *const c_char, shell: *const c_char, #[cfg(target_os = "macos")] expire: i64,
    }
    extern "C" { fn getpwnam_r(name: *const c_char, pwd: *mut Passwd, buf: *mut c_char, n: usize, out: *mut *const Passwd) -> c_int; }
    let name = CString::new(name).ok()?;
    let (mut pwd, mut buf, mut found) = (MaybeUninit::<Passwd>::uninit(), [0 as c_char; 16384], ptr::null());
    // SAFETY: every pointer is to a live local; when `found` is set, `pwd` is filled and its strings are in `buf`.
    let failed = unsafe { getpwnam_r(name.as_ptr(), pwd.as_mut_ptr(), buf.as_mut_ptr(), buf.len(), &mut found) } != 0;
    (!failed && !found.is_null()).then(|| unsafe { PathBuf::from(OsStr::from_bytes(CStr::from_ptr((*found).dir).to_bytes())) })
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn user_home(_name: &str) -> Option<PathBuf> { None }

/// `{"dir": …, "files": [{"name", "modified", "text"}, …]}`: every `*.json` of dir whose name does not start
/// with a dot, sorted by name, with its modification time in whole Unix seconds. A file that cannot be read,
/// or is not UTF-8, carries `"error"` instead of `"text"`; a folder that cannot be read gives no files and an
/// `"error"`.
pub fn feed(dir: &Path) -> String {
    let mut out = format!("{{\"dir\":{},\"files\":[", quote(&dir.to_string_lossy()));
    match fs::read_dir(dir) {
        Ok(entries) => {
            let mut names: Vec<OsString> = entries
                .filter_map(|entry| entry.ok().map(|entry| entry.file_name()))
                .filter(|name| {
                    let shown = name.to_string_lossy();
                    shown.ends_with(".json") && !shown.starts_with('.')
                })
                .collect();
            names.sort();
            let files: Vec<String> = names.iter().filter_map(|name| file(&dir.join(name))).collect();
            out.push_str(&files.join(","));
            out.push_str("]}");
        }
        Err(why) => out.push_str(&format!("],\"error\":{}}}", quote(&format!("{} cannot be read: {}", dir.display(), why)))),
    }
    out
}

/// One file's entry, or None for what is no file (a folder named *.json).
fn file(path: &Path) -> Option<String> {
    let name = path.file_name()?;
    let (modified, body) = match fs::metadata(path) {
        Ok(meta) if !meta.is_file() => return None,
        Ok(meta) => (meta.modified().ok(), read(path)),
        Err(why) => (fs::symlink_metadata(path).and_then(|meta| meta.modified()).ok(),
                     Err(format!("cannot be read: {why}"))),
    };
    let body = match (name.to_str(), body) {
        (None, _) => format!("\"error\":{}", quote("its name is not UTF-8")),
        (Some(_), Ok(text)) => format!("\"text\":{}", quote(&text)),
        (Some(_), Err(why)) => format!("\"error\":{}", quote(&why)),
    };
    let modified = modified.map_or_else(|| "null".to_string(), |at| seconds(at).to_string());
    Some(format!("{{\"name\":{},\"modified\":{},{}}}", quote(&name.to_string_lossy()), modified, body))
}

fn read(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|why| format!("cannot be read: {why}"))?;
    String::from_utf8(bytes).map_err(|_| "not UTF-8".to_string())
}

/// Whole Unix seconds, truncated toward zero as Python's `int(st_mtime)` is.
fn seconds(at: SystemTime) -> i64 {
    match at.duration_since(UNIX_EPOCH) {
        Ok(after) => after.as_secs() as i64,
        Err(before) => -(before.duration().as_secs() as i64),
    }
}

/// text as a JSON string.
fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ch if (ch as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => out.push(ch),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// A fresh folder of this test's own, removed when it ends.
    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Scratch {
            let path = std::env::temp_dir().join(format!("ceo-app-feed-{}-{}", std::process::id(), name));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Scratch(path)
        }

        fn put(&self, name: &str, bytes: &[u8], at: u64) {
            let path = self.0.join(name);
            fs::write(&path, bytes).unwrap();
            fs::File::options().write(true).open(&path).unwrap()
                .set_modified(UNIX_EPOCH + Duration::from_secs(at)).unwrap();
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn a_string_is_quoted_as_json_with_every_control_character_escaped() {
        assert_eq!(quote("plain"), "\"plain\"");
        assert_eq!(quote("a \"b\" \\ c\n\r\t\u{1}"), "\"a \\\"b\\\" \\\\ c\\n\\r\\t\\u0001\"");
        assert_eq!(quote("«Студия» ✓"), "\"«Студия» ✓\"");
    }

    #[test]
    fn the_board_is_under_ceo_home_else_under_the_home_folder() {
        let some = |text: &str| Some(OsString::from(text));
        let sam = |name: &str| (name == "sam").then(|| PathBuf::from("/people/sam/"));
        assert_eq!(board_dir_of(some("/srv/ceo"), some("/h"), sam), PathBuf::from("/srv/ceo/board"));
        assert_eq!(board_dir_of(None, some("/h"), sam), PathBuf::from("/h/.ceo/board"));
        assert_eq!(board_dir_of(some(""), some("/h"), sam), PathBuf::from("/h/.ceo/board"));
        assert_eq!(board_dir_of(some("~/elsewhere"), some("/h"), sam), PathBuf::from("/h/elsewhere/board"));
        assert_eq!(board_dir_of(some("~sam/.ceo"), some("/h"), sam), PathBuf::from("/people/sam/.ceo/board"));
        assert_eq!(board_dir_of(some("~lee/.ceo"), some("/h"), sam), PathBuf::from("~lee/.ceo/board"));
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn a_named_user_is_found_in_the_user_database_and_a_stranger_is_not() {
        assert_eq!((user_home("root").map(|dir| dir.is_absolute()), user_home("no such user")), (Some(true), None));
    }

    #[test]
    fn every_status_file_is_listed_by_name_with_its_time_and_text_and_nothing_else_is() {
        let scratch = Scratch::new("listed");
        scratch.put("b.json", "{\"ceo\": \"b «x»\"}\n".as_bytes(), 1_762_244_937);
        scratch.put("a.json", b"{", 1_762_244_900);
        scratch.put("latin.json", b"{\"ceo\": \"\xff\"}", 1_762_244_000);
        for other in [".a.json", "a.json.new", "fleet.log", "index.html"] {
            scratch.put(other, b"{}", 1);
        }
        fs::create_dir(scratch.0.join("folder.json")).unwrap();
        let dir = scratch.0.to_string_lossy().into_owned();
        assert_eq!(feed(&scratch.0), format!(
            "{{\"dir\":{},\"files\":[{},{},{}]}}", quote(&dir),
            "{\"name\":\"a.json\",\"modified\":1762244900,\"text\":\"{\"}",
            "{\"name\":\"b.json\",\"modified\":1762244937,\"text\":\"{\\\"ceo\\\": \\\"b «x»\\\"}\\n\"}",
            "{\"name\":\"latin.json\",\"modified\":1762244000,\"error\":\"not UTF-8\"}"));
    }

    #[test]
    fn a_folder_that_cannot_be_read_gives_no_files_and_says_why() {
        let scratch = Scratch::new("missing");
        let missing = scratch.0.join("nowhere");
        let got = feed(&missing);
        assert!(got.starts_with(&format!("{{\"dir\":{},\"files\":[],\"error\":\"", quote(&missing.to_string_lossy()))), "{got}");
        assert!(got.contains("cannot be read"), "{got}");
    }
}
