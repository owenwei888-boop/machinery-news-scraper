# Wrapper for Task Scheduler. Native Unicode handling.
# Runs the machinery news scraper with AI enrichment.

$ErrorActionPreference = "Continue"

$ProjectDir = "e:\OneDrive - 华兴科技公司\code space\trae\machinery-news-scraper"
$LogFile   = Join-Path $ProjectDir "logs\scraper.log"
$EnvFile   = Join-Path $ProjectDir ".env"
$Python    = Join-Path $ProjectDir ".venv\Scripts\python.exe"

# Ensure log dir
$LogDir = Split-Path $LogFile -Parent
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Log-Line([string]$msg) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Value "[$stamp] $msg" -Encoding UTF8
}

Log-Line "===== RUN START ====="
Log-Line "cwd: $(Get-Location)"
Log-Line "project_dir: $ProjectDir"

# Sanity checks
if (-not (Test-Path $ProjectDir)) { Log-Line "ERROR: project dir missing"; exit 1 }
if (-not (Test-Path $Python))     { Log-Line "ERROR: python missing: $Python"; exit 1 }
if (-not (Test-Path $EnvFile))    { Log-Line "ERROR: .env missing"; exit 1 }

# Load .env
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -le 0) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim()
    Set-Item -Path ("Env:" + $k) -Value $v
}
Log-Line "WP_URL: $env:WP_URL"
Log-Line "GEMINI_API_KEY prefix: $($env:GEMINI_API_KEY.Substring(0, [Math]::Min(8, $env:GEMINI_API_KEY.Length)))..."

# Make package importable (Python 3.12 + Chinese path workaround)
$env:PYTHONPATH = Join-Path $ProjectDir "src"

# Run scraper
Set-Location $ProjectDir
$args = @(
    "-m", "machinery_news_scraper",
    "run",
    "--source", "all",
    "--ai-provider", "gemini",
    "--ai-model", "gemini-2.5-flash-lite",
    "--auto-publish-threshold", "3",
    "--ai-rpm", "10",
    "--map-categories"
)

# Tee output to log AND capture exit code
& $Python @args 2>&1 | ForEach-Object {
    Add-Content -Path $LogFile -Value $_ -Encoding UTF8
}
$exitCode = $LASTEXITCODE

Log-Line "===== RUN END (exit $exitCode) ====="
exit $exitCode
