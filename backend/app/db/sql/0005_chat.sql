-- =====================================================================
-- 0005_chat.sql
--
-- The in-portal AI assistant's own storage: conversations, the messages
-- in them, and metadata about the tools each answer called.
--
-- What is deliberately NOT here: the tool RESULTS. Storing the rows a tool
-- returned would create a second copy of customer data sitting outside the
-- authority model that produced it - exactly the thing this design exists
-- to avoid. We keep the tool name, its arguments and a row count, which is
-- what an audit needs, and nothing that a leak would hand over.
--
-- Placeholder legend is in 0001_initial.sql.
-- =====================================================================


-- ---------------------------------------------------- chat_conversations
CREATE TABLE chat_conversations (
    id          {{UUID}}     PRIMARY KEY,
    user_id     {{UUID}}     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(120),

    -- A hash of the things that decide what this user may see (role, manager,
    -- department headship). Re-derived on every turn: when it no longer
    -- matches, the history is replayed as text only, so results fetched under
    -- the old permissions are not fed back to the model.
    permission_fingerprint VARCHAR(64) NOT NULL,

    created_at  {{TS}}       NOT NULL DEFAULT {{NOW}},
    updated_at  {{TS}}       NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_chat_conversations_user
    ON chat_conversations (user_id, updated_at);


-- -------------------------------------------------------- chat_messages
CREATE TABLE chat_messages (
    id               {{UUID}}    PRIMARY KEY,
    conversation_id  {{UUID}}    NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,
    content          TEXT        NOT NULL,

    -- Recorded so "what does this feature cost" is a query, not a guess.
    input_tokens     INTEGER,
    output_tokens    INTEGER,

    created_at       {{TS}}      NOT NULL DEFAULT {{NOW}},

    CONSTRAINT ck_chat_messages_role CHECK (role IN ('USER', 'ASSISTANT'))
);

CREATE INDEX ix_chat_messages_conversation
    ON chat_messages (conversation_id, created_at);


-- ------------------------------------------------------ chat_tool_calls
CREATE TABLE chat_tool_calls (
    id           {{UUID}}     PRIMARY KEY,
    message_id   {{UUID}}     NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    tool_name    VARCHAR(60)  NOT NULL,

    -- The arguments the model asked for - never the rows that came back.
    arguments    {{JSON}},
    row_count    INTEGER,

    -- TRUE, not 1: SQLite accepts either, PostgreSQL refuses an integer
    -- default on a boolean. Corrected in place (2026-09) because the old text
    -- could never have run on PostgreSQL, so no PostgreSQL database holds it.
    ok           {{BOOL}}     NOT NULL DEFAULT TRUE,
    error_code   VARCHAR(40),
    duration_ms  INTEGER,

    created_at   {{TS}}       NOT NULL DEFAULT {{NOW}}
);

CREATE INDEX ix_chat_tool_calls_message
    ON chat_tool_calls (message_id, created_at);
