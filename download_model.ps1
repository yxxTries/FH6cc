# Downloads an offline English Vosk model for voice commands.
#
# Default: the lgraph model (~128 MB) — much better at distinguishing spoken
# numbers (one vs. three vs. nine) at essentially the same speed for our tiny
# grammar. Pass -Small to grab the lighter 40 MB model instead.
param([switch]$Small)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$models = Join-Path $root "models"

if ($Small) {
    $name = "vosk-model-small-en-us-0.15"
    $size = "~40 MB"
} else {
    $name = "vosk-model-en-us-0.22-lgraph"
    $size = "~128 MB"
}
$target = Join-Path $models $name

if (Test-Path $target) {
    Write-Host "Model already present at $target"
    exit 0
}

New-Item -ItemType Directory -Force $models | Out-Null
$zip = Join-Path $models "vosk-model.zip"

Write-Host "Downloading Vosk model $name ($size)..."
Invoke-WebRequest "https://alphacephei.com/vosk/models/$name.zip" -OutFile $zip

Write-Host "Extracting..."
Expand-Archive $zip -DestinationPath $models -Force
Remove-Item $zip

Write-Host "Done. Voice control is ready: $target"
