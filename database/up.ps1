$ProjectRoot = Resolve-Path "$PSScriptRoot\.."

docker compose `
    -f "$PSScriptRoot\docker-compose.yml" `
    --env-file "$ProjectRoot\.env" `
    up -d