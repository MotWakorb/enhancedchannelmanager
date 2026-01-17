# Rebuild Docker Container with Latest Code

## To apply the fixes (v0.7.3-0109), you need to rebuild your container:

### Option 1: Rebuild and Restart (Recommended)
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Option 2: Quick Rebuild (if you haven't changed dependencies)
```bash
docker-compose down
docker-compose build
docker-compose up -d
```

### Option 3: Pull latest image (if using pre-built images)
```bash
docker-compose pull
docker-compose up -d
```

## What got fixed in v0.7.3-0109:
1. Frontend now sends `{}` instead of `undefined` body when deleting all orphaned groups
2. Backend has extensive `[DELETE-ORPHANED]` debug logging
3. Backend has `[AUTO-REORDER]` debug logging
4. Probe history is now persistent in `/config/probe_history.json`

## After rebuilding, you should see:
- `[DELETE-ORPHANED]` logs when you try to delete orphaned groups
- No more 422 errors
- Successful deletion of orphaned groups
