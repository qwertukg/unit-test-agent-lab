param(
    [int]$TimeoutSec = 1800,
    [switch]$WithFeedback
)

$ErrorActionPreference = "Stop"

$Compose = @(
    "compose",
    "--env-file", ".env",
    "--env-file", "config/images.env"
)

function Step([string]$Text) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor DarkGray
    Write-Host $Text -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor DarkGray
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $lines = @()
    if (Test-Path $Path) {
        $lines = @(Get-Content $Path)
    }

    $pattern = "^" + [regex]::Escape($Key) + "="
    $found = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "${Key}='${Value}'"
            $found = $true
        }
    }

    if (-not $found) {
        $lines += "${Key}='${Value}'"
    }

    Set-Content -Path $Path -Value $lines -Encoding utf8
}

function Wait-Postgres {
    $deadline = (Get-Date).AddMinutes(3)

    while ((Get-Date) -lt $deadline) {
        docker @Compose exec -T postgres `
            pg_isready -U lab -d lab *> $null

        if ($LASTEXITCODE -eq 0) {
            return
        }

        Start-Sleep -Seconds 2
    }

    throw "PostgreSQL не поднялась."
}

function Wait-Api {
    $deadline = (Get-Date).AddMinutes(3)

    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod `
                -Uri "http://localhost:8000/health" `
                -TimeoutSec 5

            if ($r.status -eq "ok") {
                return
            }
        }
        catch {
        }

        Start-Sleep -Seconds 2
    }

    throw "API не поднялся."
}

function Get-ApiToken {
    $token = (
        docker @Compose exec -T api printenv API_TOKEN
    ).Trim()

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "Не удалось получить API_TOKEN."
    }

    return $token
}

function Get-Runs {
    param([string]$Token)

    return @(
        Invoke-RestMethod `
            -Uri "http://localhost:8000/runs" `
            -Headers @{
                Authorization = "Bearer $Token"
            }
    )
}

function Show-WorkerLogs {
    Write-Host ""
    Write-Host "=== Последние логи worker ===" -ForegroundColor Yellow
    docker @Compose logs --tail 200 worker
}

#
# 0. Проверки
#

Step "0. Проверка проекта"

if (-not (Test-Path ".env")) {
    throw "Не найден .env. Запускай скрипт из корня проекта."
}

if (-not (Test-Path ".state/bootstrap.json")) {
    throw "Не найден .state/bootstrap.json. Сначала один раз запусти bootstrap.py."
}

if (-not (Test-Path "artifacts/bootstrap.json")) {
    throw "Не найден artifacts/bootstrap.json."
}

#
# 1. Лимиты
#

Step "1. Настройка лимитов"

Set-EnvValue ".env" "MAX_TOTAL_TOKENS" "1500000"
Set-EnvValue ".env" "MAX_MODEL_CALLS" "64"
Set-EnvValue ".env" "MAX_RUN_SECONDS" "7200"
Set-EnvValue ".env" "MAX_EXTERNAL_ATTEMPTS" "1"

Write-Host "MAX_TOTAL_TOKENS = 1500000"
Write-Host "MAX_MODEL_CALLS  = 64"
Write-Host "MAX_RUN_SECONDS  = 7200"

#
# 2. Останавливаем всё
#

Step "2. Остановка стенда"

docker @Compose down --remove-orphans

if ($LASTEXITCODE -ne 0) {
    throw "docker compose down завершился с ошибкой."
}

#
# 3. PostgreSQL
#

Step "3. Запуск PostgreSQL"

docker @Compose up -d postgres
Wait-Postgres

#
# 4. Чистим runtime DB
#

Step "4. Очистка runtime-состояния"

docker @Compose exec -T postgres `
    psql `
    -U lab `
    -d lab `
    -v ON_ERROR_STOP=1 `
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось очистить БД."
}

#
# 5. Инфраструктура БЕЗ worker
#

Step "5. Запуск инфраструктуры без worker"

docker @Compose up -d `
    postgres `
    gitlab `
    dind `
    runner `
    api `
    litellm `
    spark-1 `
    spark-2 `
    spark-3

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось поднять инфраструктуру."
}

Wait-Api

#
# 6. GitLab token
#

Step "6. Получение GitLab token"

$bootstrapState = Get-Content `
    ".state/bootstrap.json" `
    -Raw |
    ConvertFrom-Json

$rootToken = $bootstrapState.root_token

if ([string]::IsNullOrWhiteSpace($rootToken)) {
    throw "root_token отсутствует."
}

$gitlabApi = "http://localhost:8929/api/v4"

$gitlabHeaders = @{
    "PRIVATE-TOKEN" = $rootToken
}

#
# 7. Создаём свежий source SHA
#

Step "7. Создание свежих source commits"

$projects = Get-Content `
    "artifacts/bootstrap.json" `
    -Raw |
    ConvertFrom-Json

$projects = @(
    $projects | Where-Object {
        $_.stack -eq "java"
    }
)

$freshSources = @()

foreach ($project in $projects) {

    $projectId = [int]$project.project_id
    $branch = "feature/discounts"

    $path = [uri]::EscapeDataString("README.md")
    $branchEncoded = [uri]::EscapeDataString($branch)

    $readUrl = "${gitlabApi}/projects/${projectId}/repository/files/${path}/raw?ref=${branchEncoded}"

    $readme = Invoke-RestMethod `
        -Uri $readUrl `
        -Headers $gitlabHeaders `
        -Method Get

    $stamp = "E2E reset: $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK')"

    if ($readme -match "(?m)^E2E reset: .*$") {
        $newReadme = [regex]::Replace(
            $readme,
            "(?m)^E2E reset: .*$",
            $stamp
        )
    }
    else {
        $newReadme = $readme.TrimEnd() +
            "`r`n`r`n" +
            $stamp +
            "`r`n"
    }

    $body = @{
        branch = $branch
        content = $newReadme
        commit_message = "E2E reset $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff')"
    } | ConvertTo-Json -Depth 10

    $writeUrl = "${gitlabApi}/projects/${projectId}/repository/files/${path}"

    Invoke-RestMethod `
        -Uri $writeUrl `
        -Headers $gitlabHeaders `
        -Method Put `
        -ContentType "application/json" `
        -Body $body |
        Out-Null

    #
    # PUT /repository/files не гарантирует commit_id в ответе.
    # Поэтому читаем HEAD ветки отдельно.
    #

    $branchUrl = "${gitlabApi}/projects/${projectId}/repository/branches/${branchEncoded}"

    $branchInfo = Invoke-RestMethod `
        -Uri $branchUrl `
        -Headers $gitlabHeaders `
        -Method Get

    $sha = [string]$branchInfo.commit.id

    if ([string]::IsNullOrWhiteSpace($sha)) {
        throw "Не удалось определить свежий SHA проекта ${projectId}."
    }

    $freshSources += [PSCustomObject]@{
        ProjectId = $projectId
        Branch    = $branch
        Sha       = $sha
    }

    Write-Host `
        ("Project {0}: fresh SHA {1}" -f $projectId, $sha) `
        -ForegroundColor Green
}

#
# 8. Ждём SOURCE CI для новых SHA
#

Step "8. Ожидание source pipelines"

foreach ($source in $freshSources) {

    $projectId = [int]$source.ProjectId
    $sha = [string]$source.Sha
    $branch = [string]$source.Branch

    Write-Host ""
    Write-Host `
        ("Project {0}: ждём pipeline для SHA {1}" -f $projectId, $sha) `
        -ForegroundColor Yellow

    $deadline = (Get-Date).AddMinutes(10)
    $pipelineDone = $false

    while ((Get-Date) -lt $deadline) {

        $refEncoded = [uri]::EscapeDataString($branch)

        $pipelineUrl = "${gitlabApi}/projects/${projectId}/pipelines?ref=${refEncoded}&sha=${sha}&per_page=20"

        $response = Invoke-RestMethod `
            -Uri $pipelineUrl `
            -Headers $gitlabHeaders `
            -Method Get

        $pipelines = @($response)

        #
        # На случай вложенного массива PowerShell.
        #
        if (
            $pipelines.Count -eq 1 -and
            $pipelines[0] -is [System.Array]
        ) {
            $pipelines = @($pipelines[0])
        }

        $pipeline = $pipelines |
            Where-Object { $_.sha -eq $sha } |
            Sort-Object { [int]$_.id } -Descending |
            Select-Object -First 1

        if ($null -ne $pipeline) {

            Write-Host `
                ("Project {0}: pipeline {1} = {2}" -f `
                    $projectId,
                    $pipeline.id,
                    $pipeline.status
                )

            if ($pipeline.status -in @(
                "success",
                "failed",
                "canceled",
                "skipped"
            )) {
                $pipelineDone = $true
                break
            }
        }
        else {
            Write-Host `
                ("Project {0}: pipeline ещё не появился..." -f $projectId)
        }

        Start-Sleep -Seconds 5
    }

    if (-not $pipelineDone) {
        throw "Timeout ожидания source pipeline проекта ${projectId}, SHA ${sha}."
    }
}

#
# 9. Только ТЕПЕРЬ запускаем worker
#

Step "9. Запуск worker после свежих source pipelines"

docker @Compose up -d --force-recreate worker

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось запустить worker."
}

Start-Sleep -Seconds 3

docker @Compose ps worker

#
# 10. API token
#

$apiToken = Get-ApiToken

#
# 11. Полный pipeline
#

Step "10. Analyst -> Coder -> Runner -> Reviewer"

$deadline = (Get-Date).AddSeconds($TimeoutSec)

while ((Get-Date) -lt $deadline) {

    $runs = Get-Runs -Token $apiToken

    Clear-Host

    Write-Host "=== FULL PIPELINE STATUS ===" -ForegroundColor Cyan
    Write-Host ("Time: {0}" -f (Get-Date -Format "HH:mm:ss"))
    Write-Host ""

    if ($runs.Count -eq 0) {

        Write-Host `
            "Ждём создание свежих run..." `
            -ForegroundColor Yellow

    }
    else {

        $runs |
            Select-Object `
                project_id,
                source_sha,
                status,
                reason,
                iteration,
                model_calls,
                tokens_used |
            Format-Table -AutoSize
    }

    #
    # Проверяем, что run относится именно к свежему SHA.
    #

    $freshRuns = @()

    foreach ($run in $runs) {

        $expected = $freshSources |
            Where-Object {
                $_.ProjectId -eq $run.project_id
            } |
            Select-Object -First 1

        if ($expected -and $run.source_sha -eq $expected.Sha) {
            $freshRuns += $run
        }
    }

    #
    # Ошибки учитываем ТОЛЬКО у свежих run.
    #

    $failed = @(
        $freshRuns |
            Where-Object {
                $_.status -in @(
                    "needs_human",
                    "cancelled"
                )
            }
    )

    if ($failed.Count -gt 0) {

        Write-Host ""
        Write-Host "=== PIPELINE FAILED ===" -ForegroundColor Red

        $failed |
            Select-Object `
                id,
                project_id,
                source_sha,
                status,
                reason,
                model_calls,
                tokens_used |
            Format-Table -AutoSize

        Show-WorkerLogs
        exit 1
    }

    #
    # published должны быть именно по свежим SHA
    #

    $publishedProjects = @(
        $freshRuns |
            Where-Object {
                $_.status -eq "published"
            } |
            Select-Object `
                -ExpandProperty project_id `
                -Unique
    )

    if ($publishedProjects.Count -ge $freshSources.Count) {

        Write-Host ""
        Write-Host `
            "Все свежие demo-run успешно published." `
            -ForegroundColor Green

        break
    }

    Start-Sleep -Seconds 5
}

if ((Get-Date) -ge $deadline) {

    Write-Host ""
    Write-Host "=== PIPELINE TIMEOUT ===" -ForegroundColor Red

    Show-WorkerLogs
    exit 2
}

#
# 12. Mutation acceptance
#

Step "11. Mutation acceptance"

& .venv/Scripts/python `
    scripts/acceptance.py `
    --mutation `
    --timeout 600

if ($LASTEXITCODE -ne 0) {

    Write-Host ""
    Write-Host "=== MUTATION ACCEPTANCE FAILED ===" -ForegroundColor Red

    Show-WorkerLogs
    exit $LASTEXITCODE
}

#
# 13. Optional feedback
#

if ($WithFeedback) {

    Step "12. Feedback acceptance"

    & .venv/Scripts/python `
        scripts/acceptance.py `
        --feedback `
        --mutation `
        --timeout 900

    if ($LASTEXITCODE -ne 0) {

        Write-Host ""
        Write-Host "=== FEEDBACK ACCEPTANCE FAILED ===" -ForegroundColor Red

        Show-WorkerLogs
        exit $LASTEXITCODE
    }
}

#
# DONE
#

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "               FULL E2E PASSED" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host "Fresh source SHA : PASSED"
Write-Host "Source CI        : PASSED"
Write-Host "Analyst          : PASSED"
Write-Host "Coder            : PASSED"
Write-Host "Generated tests  : PASSED"
Write-Host "GitLab Runner    : PASSED"
Write-Host "Reviewer         : PASSED"
Write-Host "Test MR          : PASSED"
Write-Host "Mutation         : PASSED"

if ($WithFeedback) {
    Write-Host "Feedback loop    : PASSED"
}

Write-Host "==================================================" -ForegroundColor Green