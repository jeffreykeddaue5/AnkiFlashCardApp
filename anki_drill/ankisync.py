"""
ankisync.py
Optional AnkiWeb sync on startup, so the drill list is current without opening Anki first.

This is the one place the app writes to a collection, and it only ever does what Anki's own
Sync button does: a normal incremental sync plus a media sync. It uses the official `anki`
library (pip install anki, pinned to the same version as your Anki desktop) and the sync
login Anki already saved in prefs21.db, so no password is asked for or stored here.

It is skipped, never forced, when:
  - the `anki` library is not installed (the app then behaves exactly as before),
  - Anki desktop is running (it owns the collection, and syncs it itself),
  - the profile has never logged in to AnkiWeb,
  - AnkiWeb asks for a full upload/download (that one-way choice is left to Anki).
"""

import os
import pickle
import sqlite3
import time

MEDIA_SYNC_TIMEOUT_SECS = 120


def ankiAvailable():
    """True if the official anki library can be imported."""
    try:
        import anki.collection  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
    except ImportError:
        return False
    return True


def ankiDesktopRunning():
    """True if an Anki desktop process is running for this user."""
    uid = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                args = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        # Snap/pip installs run `python3 .../bin/anki`, the official tarball runs `anki`
        # directly, and `python -m aqt` is the developer form.
        for arg in args[:3]:
            if os.path.basename(arg) == "anki" or arg == "aqt":
                return True
    return False


def savedLogin(root, profileName):
    """Return (hkey, endpoint, syncMedia) Anki saved for this profile, or None if it never logged in."""
    prefsPath = os.path.join(root, "prefs21.db")
    if not os.path.exists(prefsPath):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % prefsPath, uri=True)
    try:
        row = conn.execute(
            "SELECT cast(data AS blob) FROM profiles WHERE name = ?", (profileName,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        prefs = pickle.loads(row[0])
    except Exception:  # pylint: disable=broad-except
        return None
    hkey = prefs.get("syncKey") if isinstance(prefs, dict) else None
    if not hkey:
        return None
    endpoint = prefs.get("currentSyncUrl") or prefs.get("customSyncUrl") or None
    return hkey, endpoint, prefs.get("syncMedia", True) is not False


def syncProfile(profile):
    """Sync one profile with AnkiWeb. Returns a short human-readable result."""
    login = savedLogin(profile.root, profile.name)
    if login is None:
        return "%s: not logged in to AnkiWeb in Anki, skipped" % profile.name
    hkey, endpoint, syncMedia = login

    # pylint: disable=import-outside-toplevel
    from anki.collection import Collection
    from anki.sync import SyncAuth
    from anki.sync_pb2 import SyncCollectionResponse

    auth = SyncAuth(hkey=hkey, endpoint=endpoint) if endpoint else SyncAuth(hkey=hkey)
    col = Collection(profile.collectionPath)
    try:
        out = col.sync_collection(auth, sync_media=False)
        if out.new_endpoint:
            auth = SyncAuth(hkey=hkey, endpoint=out.new_endpoint)

        if out.required in (SyncCollectionResponse.FULL_SYNC,
                            SyncCollectionResponse.FULL_DOWNLOAD,
                            SyncCollectionResponse.FULL_UPLOAD):
            return ("%s: AnkiWeb needs a full sync. Open Anki and sync there once"
                    % profile.name)

        if syncMedia:
            col.sync_media(auth)
            deadline = time.monotonic() + MEDIA_SYNC_TIMEOUT_SECS
            while col.media_sync_status().active and time.monotonic() < deadline:
                time.sleep(0.2)
            if col.media_sync_status().active:
                col.abort_media_sync()

        return "%s: synced with AnkiWeb" % profile.name
    finally:
        col.close(downgrade=False)


def syncAll(profiles):
    """Sync every profile that has a saved AnkiWeb login. Returns a list of result lines."""
    if not ankiAvailable():
        return ["AnkiWeb sync off: the anki library is not installed"]
    if ankiDesktopRunning():
        return ["Anki is open, so it handles syncing. Skipped"]
    results = []
    for profile in profiles:
        try:
            results.append(syncProfile(profile))
        except Exception as exc:  # pylint: disable=broad-except
            results.append("%s: sync failed (%s)" % (profile.name, exc))
    return results
