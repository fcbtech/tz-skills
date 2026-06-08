# Outline API Reference

## Complete Endpoint List

### Collections
| Endpoint | Description |
|----------|-------------|
| `collections.list` | List all collections |
| `collections.info` | Get collection details |
| `collections.create` | Create a new collection |
| `collections.update` | Update collection |
| `collections.delete` | Delete collection |
| `collections.documents` | List documents in collection tree |

### Documents
| Endpoint | Description |
|----------|-------------|
| `documents.list` | List documents (paginated) |
| `documents.info` | Get document by ID |
| `documents.search` | Full-text search |
| `documents.create` | Create document |
| `documents.update` | Update document |
| `documents.delete` | Delete (archive) document |
| `documents.move` | Move to different collection/parent |
| `documents.archive` | Archive document |
| `documents.restore` | Restore archived document |
| `documents.star` | Star a document |
| `documents.unstar` | Unstar a document |
| `documents.viewed` | List recently viewed |
| `documents.drafts` | List user's drafts |

### Attachments
| Endpoint | Description |
|----------|-------------|
| `attachments.create` | Create attachment upload URL |
| `attachments.delete` | Delete attachment |

### Search
| Endpoint | Description |
|----------|-------------|
| `documents.search` | Search documents |

## Query Parameters

### documents.list
```json
{
  "collectionId": "uuid",     // Filter by collection
  "parentDocumentId": "uuid", // Filter by parent
  "sort": "updatedAt",        // Sort field
  "direction": "DESC",        // ASC or DESC
  "limit": 25,                // Max results (1-100)
  "offset": 0                 // Pagination offset
}
```

### documents.search
```json
{
  "query": "search term",     // Required search string
  "collectionId": "uuid",     // Limit to collection
  "userId": "uuid",           // Filter by author
  "dateFilter": "week",       // day, week, month, year
  "limit": 25,
  "offset": 0
}
```

## Error Handling

Errors return:
```json
{
  "ok": false,
  "error": "error_code",
  "message": "Human readable message",
  "status": 400
}
```

Common error codes:
- `authentication_required`: Invalid or missing token
- `authorization_error`: No permission for action
- `validation_error`: Invalid request parameters
- `not_found`: Document/collection not found

## Rate Limits

- 1000 requests per minute per API token
- Bulk operations should add small delays between requests

## Markdown Support

Outline uses CommonMark markdown with extensions:
- Tables
- Task lists `- [ ]`
- Code blocks with syntax highlighting
- Image embeds
- Internal document links `[[Document Title]]`
- Mentions `@username`

## Tips

1. **Pagination**: Always handle pagination for list operations. Check `pagination.total` to know if more results exist.

2. **Search vs List**: Use `documents.search` for finding by content, `documents.list` for browsing structure.

3. **Bulk Operations**: When creating many documents, add a small delay (50-100ms) between requests.

4. **Attachments**: Upload URL is temporary (expires in ~1 hour). Upload immediately after getting it.

5. **Document Text**: The `text` field contains full markdown content. Title should NOT be duplicated in text.
