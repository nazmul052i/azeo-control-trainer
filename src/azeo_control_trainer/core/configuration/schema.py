"""Versioned PostgreSQL schema; immutable evidence is enforced below the API."""
VERSION = 8
SQL = """
CREATE SCHEMA IF NOT EXISTS azeo_configuration;
SET LOCAL search_path TO azeo_configuration, public;
CREATE TABLE schema_version(version integer PRIMARY KEY, checksum text NOT NULL);
CREATE TABLE identities(
    name text PRIMARY KEY, token_hash text UNIQUE NOT NULL,
    administrator boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE projects(
    id uuid PRIMARY KEY, name text NOT NULL, name_key text UNIQUE NOT NULL,
    generation bigint NOT NULL DEFAULT 0, digest text NOT NULL DEFAULT '',
    mode text NOT NULL DEFAULT 'file_shadow' CHECK (mode = 'file_shadow'),
    modified timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE grants(
    project_id uuid REFERENCES projects(id), identity text REFERENCES identities(name),
    role text NOT NULL CHECK(role IN ('reader','importer')),
    PRIMARY KEY(project_id, identity)
);
CREATE TABLE objects(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    path text NOT NULL, path_key text NOT NULL, kind text NOT NULL, name text NOT NULL,
    revision integer NOT NULL, digest text NOT NULL, deleted boolean NOT NULL DEFAULT false,
    UNIQUE(project_id, path_key)
);
CREATE TABLE changes(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    command_id uuid UNIQUE NOT NULL, project_id uuid NOT NULL REFERENCES projects(id),
    generation bigint NOT NULL, actor text NOT NULL REFERENCES identities(name),
    action text NOT NULL, request_hash text NOT NULL, result jsonb NOT NULL,
    occurred timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE revisions(
    object_id uuid REFERENCES objects(id), number integer NOT NULL,
    change_id bigint NOT NULL REFERENCES changes(id), path text NOT NULL,
    digest text NOT NULL, document jsonb, content bytea NOT NULL,
    deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY(object_id, number)
);
CREATE TABLE tags(
    project_id uuid NOT NULL REFERENCES projects(id), path text NOT NULL,
    object_id uuid NOT NULL REFERENCES objects(id), data jsonb NOT NULL,
    PRIMARY KEY(project_id, path)
);
CREATE INDEX objects_project ON objects(project_id, kind, path);
CREATE INDEX changes_project ON changes(project_id, id);
CREATE INDEX tags_object ON tags(object_id);
CREATE FUNCTION immutable_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Configuration revisions and audit records are immutable'; END;
$$;
CREATE TRIGGER revisions_immutable BEFORE UPDATE OR DELETE ON revisions
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
CREATE TRIGGER changes_immutable BEFORE UPDATE OR DELETE ON changes
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
"""

# Keep applied migration text unchanged: its checksum is part of the contract.
# The catalog's original JSON stays canonical; this is a generated search index.
MIGRATIONS = ((1, SQL), (2, """
ALTER TABLE tags ADD COLUMN search_text text GENERATED ALWAYS AS
    (lower(path || ' ' || coalesce(data->>'description',''))) STORED;
"""), (3, """
CREATE TABLE catalogs(
    project_id uuid PRIMARY KEY REFERENCES projects(id),
    version integer NOT NULL, document jsonb NOT NULL,
    built_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""), (4, """
ALTER TABLE projects DROP CONSTRAINT projects_mode_check;
ALTER TABLE projects ADD CONSTRAINT projects_mode_check
    CHECK(mode IN ('file_shadow','repository'));
ALTER TABLE projects ADD COLUMN origin jsonb;
ALTER TABLE grants DROP CONSTRAINT grants_role_check;
ALTER TABLE grants ADD CONSTRAINT grants_role_check
    CHECK(role IN ('reader','importer','engineer'));
CREATE TABLE edit_leases(
    project_id uuid NOT NULL REFERENCES projects(id), path_key text NOT NULL,
    path text NOT NULL, actor text NOT NULL REFERENCES identities(name),
    session uuid NOT NULL, expires_at timestamptz NOT NULL,
    PRIMARY KEY(project_id,path_key)
);
"""), (5, """
CREATE TABLE release_previews(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    actor text NOT NULL REFERENCES identities(name), manifest jsonb NOT NULL,
    bundle jsonb NOT NULL, created timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE releases(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    number bigint NOT NULL, command_id uuid UNIQUE NOT NULL,
    actor text NOT NULL REFERENCES identities(name), request_hash text NOT NULL,
    reason text NOT NULL, manifest jsonb NOT NULL, bundle jsonb NOT NULL,
    created timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(project_id,number)
);
CREATE TRIGGER releases_immutable BEFORE UPDATE OR DELETE ON releases
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
CREATE TRIGGER release_previews_immutable BEFORE UPDATE OR DELETE ON release_previews
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
CREATE TABLE runtime_targets(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL, identity text UNIQUE NOT NULL REFERENCES identities(name),
    boot uuid, heartbeat timestamptz, online boolean NOT NULL DEFAULT false,
    report jsonb NOT NULL DEFAULT '{}', created timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(project_id,name)
);
CREATE TABLE deployment_jobs(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    release_id uuid NOT NULL REFERENCES releases(id), target_id uuid NOT NULL REFERENCES runtime_targets(id),
    actor text NOT NULL REFERENCES identities(name), command_id uuid UNIQUE NOT NULL,
    request_hash text NOT NULL, components jsonb NOT NULL,
    state text NOT NULL DEFAULT 'requested' CHECK(state IN ('requested','staged','delivered','failed')),
    attempt integer NOT NULL DEFAULT 1, receipt jsonb NOT NULL DEFAULT '{}',
    created timestamptz NOT NULL DEFAULT clock_timestamp(), modified timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX deployment_jobs_target ON deployment_jobs(target_id,created);
CREATE TABLE deployment_events(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES deployment_jobs(id), actor text NOT NULL REFERENCES identities(name),
    event text NOT NULL, evidence jsonb NOT NULL,
    occurred timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER deployment_events_immutable BEFORE UPDATE OR DELETE ON deployment_events
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
CREATE TABLE upload_previews(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    target_id uuid NOT NULL REFERENCES runtime_targets(id), actor text NOT NULL REFERENCES identities(name),
    document jsonb NOT NULL, created timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER upload_previews_immutable BEFORE UPDATE OR DELETE ON upload_previews
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
"""), (6, """
ALTER TABLE deployment_jobs DROP CONSTRAINT deployment_jobs_state_check;
ALTER TABLE deployment_jobs ADD CONSTRAINT deployment_jobs_state_check
    CHECK(state IN ('requested','staged','delivered','failed','cancelled'));
"""), (7, """
CREATE TABLE library_previews(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    actor text NOT NULL REFERENCES identities(name), document jsonb NOT NULL,
    created timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER library_previews_immutable BEFORE UPDATE OR DELETE ON library_previews
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
"""), (8, """
CREATE TABLE training_baselines(
    id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
    release_id uuid NOT NULL REFERENCES releases(id), name text NOT NULL,
    actor text NOT NULL REFERENCES identities(name), reason text NOT NULL,
    command_id uuid UNIQUE NOT NULL, request_hash text NOT NULL,
    document jsonb NOT NULL, created timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER training_baselines_immutable BEFORE UPDATE OR DELETE ON training_baselines
FOR EACH ROW EXECUTE FUNCTION immutable_evidence();
"""))
