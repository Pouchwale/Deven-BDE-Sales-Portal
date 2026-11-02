"""Back-Up & Restore (Super Admin).

    storage   where backups live, and proving the folder is usable
    pgtools   pg_dump / pg_restore / psql, run with credentials kept out of argv
    catalog   what backups exist - on the BACKUP DISK, not in the database,
              so a restore can never erase or rewind the history it relies on
    project   the "with code" ZIP: source tree + database + manifest
    engine    create and verify both kinds of backup
    restore   validate a dump or ZIP, safety backup, restore, verify, roll back
    jobs      long work runs in a background thread; progress is kept on disk
    scheduler optional daily backup with retention, inside the app process

The Windows PowerShell tools in deploy/backup keep working for this PC's
nightly backup of the LIVE database; this package is what the portal itself
runs, on Windows and on Render alike.
"""
