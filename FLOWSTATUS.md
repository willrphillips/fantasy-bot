# Living Flowcharts — status reporting

This folder was auto-detected as a project by Living Flowcharts.

To make its chart reflect live agent/process state, keep `.flowstatus.json`
in this folder up to date. Any agent or script running here may write it.

## Schema

```json
{
  "projectId": "<this-folder-name>",
  "updatedAt": "ISO-8601 timestamp",
  "nodes": {
    "<node-id>": {
      "status": "active | planned | broken | idle",
      "lastRun": "2026-05-15",
      "note": "short free-text, e.g. 'running' or last error",
      "outputUrl": "https://..."
    }
  }
}
```

Only include the nodes and fields you actually know. Node ids should match the
ids in the curated chart at `living-flowcharts/data/projects/<projectId>.json`
(if one exists). If no curated chart exists, the nodes here are rendered as a
simple left-to-right pipeline.

Delete this file and `.flowstatus.json` to opt this folder out.
