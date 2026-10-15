---
name: conversation-notes
description: Use when the user explicitly asks to open 墨记 or save selected conversation passages as highlights and side comments. Supports a local reading panel, annotations, backups and specified local chat imports.
---

# 墨记 · 对话批注

Use the conversation-notes MCP tools to open the annotation panel and save the passages the user selects.

- Call `open_notes` to open the panel. To import the current local conversation when the user requests it, obtain its actual `CODEX_THREAD_ID` from the environment and pass it as `thread_id`; never invent IDs. Pass the user's title or a short descriptive title.
- When the user provides a passage, call `import_text` with its exact text and a useful title, then `open_notes` to show the panel. Do not rewrite the passage.
- Only `import_thread` reads local conversation files, and only for the explicit thread ID the user requests. Do not scan unrelated conversations or read tool output/system messages as user-facing original text.
- Highlighters use UTF-16 string offsets, matching browser selection offsets. `quote` must exactly match the specified range. Get the saved message before calculating offsets.
- Updates require the current `version`. If an edit conflicts, preserve the user's text, retrieve the latest annotation, and reconcile; do not silently overwrite a newer revision.
- Removed annotations are soft deleted and can be restored with `deleted=false` and the latest version.
- `get_document` returns the current edited text and projected UTF-16 annotation offsets, plus message `version`, document `revision`, and the latest deletion `undo` action. Use these displayed offsets for selections. Pass the message version to `add_annotation` when available.
- At the user's request, use `delete_text` with the exact selected quote and current message version; `delete_messages` with selected message IDs and current document revision; or `delete_document` to move a whole copy into the local trash. These affect only the plugin copy and its exports, never the original Codex chat. Deleted source text and anchor positions remain stored for undo.
- `undo_delete` restores the latest active edit or deletion for that document. `list_documents` includes `trash`; `restore_document` restores a trashed copy. Deletions survive refresh and chat sync. Never erase the native session or manually purge the database as a substitute.
- Opening or refreshing an already imported local chat syncs that exact thread, filters its leading ambient browser wrapper, and shows its latest messages. Background sync preserves reading position unless already at the bottom.
- Display normalization also removes recognized leading attachment-list wrappers and extracts the actual answer strings from complete `send_user_message_question_reply` receipts. JSON escapes are decoded with original UTF-16 anchors retained. This applies to existing copies, cuts and exports; raw backup content is retained. Never remove matching words globally from the user's actual request or quoted examples.
- Use `export_notes` and `restore_notes` only at the user's request. Never send notes to external services without user instruction.
- `export_notes` accepts `pdf`, `docx`, `json`, or `markdown`. Pass the selected `document_id` for a PDF or Word export. PDF and DOCX retain formatted text, real tables, proportional images, rendered equations, highlights and numbered annotation lists. DOCX includes native Word comment anchors; PDF includes clickable margin notes. Equations are embedded as high-resolution graphics, not editable OMML. Binary downloads are returned only in `_meta.download` to the panel, not as model-visible base64. Use the panel's Export menu to download them.
- PDF, DOCX and Markdown omit deleted content. JSON v3 is a complete recovery backup including original text, trash and deletion history; restore also accepts v1 and v2. Make that distinction clear when relevant.
- The Appearance menu offers live previews for `codex` (neutral default), `paper`, and `mist`, with `auto`, `light`, or `dark` modes. `get_appearance` reads the saved choice; use `set_appearance` only for the user's requested appearance change. Preferences persist in the local database across service restarts and ports; changing the theme leaves note drafts and anchors intact. Auto follows the host's theme when supplied, otherwise the system preference.

## Interface boundaries

The public MCP Apps and Extensions APIs do not currently document native Codex transcript text-selection events or native message-level scroll targets. Do not claim these work. Selection/highlight and exact annotation navigation operate inside the plugin's own reading panel. `codex://threads/<thread-id>` opens the original chat, without claiming message-level positioning.

Data lives in `~/.codex/conversation-notes/notes.sqlite3`, outside the installed plugin cache. Source, cache updates, and reopening the UI do not clear notes.

## Rich reading and keyboard edits

- The reader renders Markdown/GFM, tables, syntax-colored code, and KaTeX math. Source offsets remain UTF-16 in the original Markdown, not the rendered text. Use the panel's selection mapping or source mode; never calculate an API anchor from rendered innerText.
- Double click a message or use Edit to open its inline Markdown text editor. Save with the visible button or Cmd/Ctrl+Enter. Preview and format buttons help edit without memorizing syntax. Local browser drafts survive accidental refresh; switching documents or exporting saves the current edit first. Cancel/Esc discard the draft. Do not overwrite a conflicting message version.
- `edit_message` requires message_id, exact new body (including an intentional empty body), and expected_version. It changes only the plugin copy, translates existing note anchors through the change and records an undo action. Fully erased note anchors are hidden until undo. Later comment text and explicit annotation deletion are preserved when undoing a body edit. Sync does not replace the edited copy.
- `delete_text` optionally accepts non-contiguous `ranges` within the exact bounding quote. The rich reader uses these ranges to retain necessary Markdown delimiters.
- Local images referenced by the imported message and native input-image attachments are snapshotted under `~/.codex/conversation-notes/images/`. `get_message_image` accepts only a reference already present in that active message or its saved attachment key. It returns binary only via _meta.download in MCP. Do not use it as an arbitrary file reader. Images are limited to 10 MiB; supported formats are PNG/JPEG/GIF/WebP and sanitized SVG. Remote images load directly in the reader after a click. Export may fetch only actual public image destinations already referenced in the active message, blocking local/private destinations and enforcing image limits; successful snapshots are then included in later backups. Unavailable images have visible explanations and an export warning. Code examples are not fetched.
- JSON v3 includes local image snapshots, source copies, body edits and reversible history. PDF/DOCX share a paper-oriented rich layout and support bold text, native tables, rendered math, embedded images and highlights/comments. Preserve the source content and projected anchors. Pagination and line wrapping can differ from the screen; do not promise pixel-identical output or native editable Word math.

## Rename existing copies

- Existing documents can be renamed from the button beside the document selector. `rename_document` requires document_id, the desired title (1–200 characters after trimming) and current expected_revision. It preserves the source thread, messages, edits, annotations and deleted state. Conflicts reject overwriting another panel's newer revision.
- Use the user’s supplied title; do not rename an existing copy merely because a feature demonstration would be convenient. Titles persist across sync, refresh and JSON backup restore, and are used in export filenames, document headings and metadata.
