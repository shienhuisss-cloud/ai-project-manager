CREATE TABLE IF NOT EXISTS user (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_version_id TEXT,
            requirement_seq INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS membership (
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS source (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            text TEXT NOT NULL,
            label TEXT,
            is_demo INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS draft (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL UNIQUE,
            parent_version_id TEXT,
            requirements_json TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS version (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            parent_version_id TEXT,
            snapshot_json TEXT NOT NULL,
            source_analysis_run_id TEXT,
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            UNIQUE(project_id, seq)
        );

        CREATE TABLE IF NOT EXISTS analysis_run (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            baseline_version_id TEXT,
            task_type TEXT NOT NULL,
            model_id TEXT,
            prompt_version TEXT,
            status TEXT NOT NULL,
            demo_mode INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS change_decision (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            change_index INTEGER NOT NULL,
            decision TEXT NOT NULL,
            revised_after TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(analysis_run_id, change_index)
        );

        CREATE TABLE IF NOT EXISTS idempotency (
            request_id TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
