---
name: outline
description: Interact with the Outline knowledge base. Use when the user asks to list, read, create, update, search, or delete documents in Outline. Also handles collections management. Triggers on keywords like "outline", "knowledge base", "wiki", "documentation site".
---

# Outline Knowledge Base Skill

Interact with the TranZact Outline instance for document and collection management.

## Configuration

This skill uses the TranZact Outline URL and expects an API token before any API call:

| Variable | Description | Example |
|---|---|---|
| `OUTLINE_BASE_URL` | Optional override for the Outline base URL | `https://outline.letstranzact.com` |
| `OUTLINE_API_TOKEN` | Personal API token (Settings -> API Tokens) | `<outline-api-token>` |

The default base URL is `https://outline.letstranzact.com`. The agent should read config in this order:

1. Environment variables.
2. `scripts/.env` adjacent to `scripts/outline_helper.py`.

Never hardcode `OUTLINE_API_TOKEN`, and never echo it to logs or chat. The repo ignores `outline/scripts/.env`.

```bash
export OUTLINE_API_TOKEN="<outline-api-token>"
```

If the token is missing, ask the user to paste the Outline API token. Then save it for future sessions:

```bash
python outline/scripts/outline_helper.py setup
```

The helper writes `outline/scripts/.env` with mode `0600`.

Use `scripts/outline_helper.py` for common operations. Read `references/reference.md` when endpoint details, parameters, error codes, or Markdown support are needed.

## API Pattern

All Outline API calls use POST requests with JSON body:

```bash
curl -s -X POST "https://outline.letstranzact.com/api/{endpoint}" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"param": "value"}'
```

## Available Operations

### 1. List Collections

```bash
curl -s -X POST "https://outline.letstranzact.com/api/collections.list" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 2. Create Collection

```bash
curl -s -X POST "https://outline.letstranzact.com/api/collections.create" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "Collection Name", "description": "Optional description"}'
```

### 3. List Documents

List all documents (paginated):
```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.list" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"limit": 25, "offset": 0}'
```

List documents in a specific collection:
```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.list" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"collectionId": "COLLECTION_UUID", "limit": 25}'
```

### 4. Search Documents

```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.search" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query": "search term", "limit": 25}'
```

### 5. Get Document (Read)

By ID:
```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.info" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"id": "DOCUMENT_UUID"}'
```

### 6. Create Document

```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.create" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "collectionId": "COLLECTION_UUID",
    "title": "Document Title",
    "text": "Markdown content here",
    "publish": true
  }'
```

With parent document (nested):
```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.create" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "collectionId": "COLLECTION_UUID",
    "parentDocumentId": "PARENT_DOC_UUID",
    "title": "Child Document",
    "text": "Content",
    "publish": true
  }'
```

### 7. Update Document

```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.update" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "DOCUMENT_UUID",
    "title": "Updated Title",
    "text": "Updated markdown content",
    "publish": true
  }'
```

### 8. Delete Document

```bash
curl -s -X POST "https://outline.letstranzact.com/api/documents.delete" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"id": "DOCUMENT_UUID"}'
```

### 9. Upload Attachment

Step 1 - Create attachment record:
```bash
curl -s -X POST "https://outline.letstranzact.com/api/attachments.create" \
  -H "Authorization: Bearer ${OUTLINE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "filename.png",
    "contentType": "image/png",
    "size": 12345
  }'
```

Step 2 - Upload file to the returned `uploadUrl` using the `form` data fields.

## Response Format

All responses have this structure:
```json
{
  "ok": true,
  "data": { ... },
  "pagination": {
    "limit": 25,
    "offset": 0,
    "total": 100
  },
  "status": 200
}
```

## Common Fields

### Document Object
- `id`: UUID of the document
- `title`: Document title
- `text`: Markdown content
- `collectionId`: Parent collection UUID
- `parentDocumentId`: Parent document UUID (if nested)
- `createdAt`, `updatedAt`: Timestamps
- `url`: Web URL path

### Collection Object
- `id`: UUID of the collection
- `name`: Collection name
- `description`: Collection description

## Usage Examples

**User**: "List all collections in outline"
**Action**: Call collections.list endpoint

**User**: "Search for documents about payments"
**Action**: Call documents.search with query "payments"

**User**: "Read the document titled 'API Guide'"
**Action**: First search for it, then call documents.info with the ID

**User**: "Create a new document in the Tech Resources collection"
**Action**: First get collection ID from collections.list, then call documents.create

**User**: "Update the content of document XYZ"
**Action**: Call documents.update with the document ID and new content
