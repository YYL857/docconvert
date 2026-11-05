# docconvert - one-click portable deployment for Windows (no admin)
#
# Hard rules:
#   - Prefer green unpack into ..\bin\  (copy the whole docconvert folder to another PC)
#   - Never msiexec /i or winget install (no admin / UAC)
#   - LibreOffice MSI: msiexec /a (administrative unpack)
#   - Runtime conversion is 100% offline after this script finishes
#
# Usage:
#   .\install.ps1                 # python deps + engines from vendor\ into bin\
#   .\install.ps1 -SkipEngines    # python deps only
#   .\install.ps1 -Engines pandoc,ffmpeg
#   .\install.ps1 -Check          # report only
#
param(
    [switch]$SkipEngines,
    [string[]]$Engines = @("libreoffice", "pandoc", "tesseract", "ffmpeg"),
    [switch]$Check
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $PSScriptRoot "vendor"
$bin = Join-Path $root "bin"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Ok($msg)   { Say "  [OK]   $msg" "Green" }
function Warn($msg) { Say "  [WARN] $msg" "Yellow" }
function Bad($msg)  { Say "  [FAIL] $msg" "Red" }
function Head($msg) { Say ""; Say "== $msg" "Cyan" }

Say ""
Say "docconvert portable deployment (no admin)" "White"
Say "root: $root"

# ---------------------------------------------------------------- python
Head "1. Python"
$py = $null
foreach ($c in @(
    "C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe",
    "python", "python3", "py"
)) {
    try {
        $v = & $c -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $major, $minor = $v.Split(".")
            if ([int]$major -ge 3 -and [int]$minor -ge 8) { $py = $c; break }
        }
    } catch { }
}
if (-not $py) {
    Bad "Python 3.8+ not found. Install from https://www.python.org/downloads/"
    Say "     Remember to tick 'Add python.exe to PATH'."
    if (-not $Check) { exit 1 }
} else {
    $ver = & $py -V 2>&1
    Ok "$ver  ($py)"
}

# ---------------------------------------------------------------- pip deps
Head "2. Python packages"
$pkgs = @("pymupdf", "python-docx", "openpyxl", "pillow", "pyyaml", "python-pptx")
if ($Check) {
    foreach ($p in $pkgs) {
        $mod = $p -replace 'pyyaml','yaml' -replace 'python-docx','docx' -replace 'python-pptx','pptx' -replace 'pymupdf','fitz'
        $has = & $py -c "import importlib.util as u;print(1 if u.find_spec('$mod') else 0)" 2>$null
        if ($has -eq "1") { Ok $p } else { Warn "$p missing" }
    }
} else {
    Say "  installing: $($pkgs -join ', ')"
    & $py -m pip install --upgrade pip --quiet 2>$null
    & $py -m pip install $pkgs
    if ($LASTEXITCODE -eq 0) { Ok "python packages ready" } else { Warn "pip reported errors, run 'pip install $($pkgs -join ' ')' manually" }
}

function Find-VendorFile($pattern) {
    if (-not (Test-Path $vendor)) { return $null }
    Get-ChildItem $vendor -Filter $pattern -File -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
}

function Ensure-Dir($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

if ($SkipEngines) {
    Head "3. Engines - skipped (-SkipEngines)"
} else {
    Head "3. Conversion engines -> $bin"
    Ensure-Dir $bin

    foreach ($e in $Engines) {
        switch ($e.ToLower()) {
            "libreoffice" {
                $soffice = Join-Path $bin "libreoffice\program\soffice.exe"
                if (Test-Path $soffice) { Ok "LibreOffice already in bin: $soffice"; break }

                $msi = Find-VendorFile "LibreOffice*.msi"
                if (-not $msi) {
                    Warn "LibreOffice: put LibreOffice*.msi into vendor\ then re-run"
                    break
                }
                $dst = Join-Path $bin "libreoffice"
                Ensure-Dir $dst
                Say "  unpacking LibreOffice via msiexec /a -> $dst ..."
                $log = Join-Path $vendor "lo_msiexec.log"
                $p = Start-Process msiexec.exe -ArgumentList @(
                    "/a", "`"$msi`"",
                    "TARGETDIR=`"$dst`"",
                    "/qn", "/norestart",
                    "/l*v", "`"$log`""
                ) -Wait -PassThru
                # msiexec /a often nests Files\LibreOffice\... — flatten if needed
                $nested = Get-ChildItem $dst -Recurse -Filter "soffice.exe" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Directory.Name -eq "program" } |
                    Select-Object -First 1
                if ($nested -and $nested.FullName -ne $soffice) {
                    $brand = $nested.Directory.Parent.FullName
                    Say "  flattening nested layout from $brand"
                    Get-ChildItem $brand | ForEach-Object {
                        $target = Join-Path $dst $_.Name
                        if (Test-Path $target) { Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue }
                        Move-Item $_.FullName $dst -Force
                    }
                }
                if (Test-Path $soffice) { Ok "LibreOffice unpacked: $soffice" }
                else { Bad "LibreOffice unpack failed (see $log), exit=$($p.ExitCode)" }
            }
            "pandoc" {
                $exe = Join-Path $bin "pandoc\pandoc.exe"
                if (Test-Path $exe) { Ok "pandoc already in bin: $exe"; break }

                $zip = Find-VendorFile "pandoc*.zip"
                if (-not $zip) { Warn "pandoc: put pandoc*-windows-x86_64.zip into vendor\"; break }
                $tmp = Join-Path $vendor "_pandoc_unpack"
                if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
                Ensure-Dir $tmp
                Say "  unpacking pandoc zip ..."
                Expand-Archive $zip -DestinationPath $tmp -Force
                $found = Get-ChildItem $tmp -Recurse -Filter "pandoc.exe" | Select-Object -First 1
                if (-not $found) { Bad "pandoc.exe not found inside zip"; break }
                $dst = Join-Path $bin "pandoc"
                if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
                Ensure-Dir $dst
                Move-Item $found.FullName (Join-Path $dst "pandoc.exe")
                # keep sibling dlls if any
                Get-ChildItem $found.Directory -File | Where-Object { $_.Name -ne "pandoc.exe" } | ForEach-Object {
                    Copy-Item $_.FullName $dst -Force
                }
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                Ok "pandoc unpacked: $(Join-Path $dst 'pandoc.exe')"
            }
            "tesseract" {
                $exe = Join-Path $bin "tesseract\tesseract.exe"
                if (Test-Path $exe) { Ok "Tesseract already in bin: $exe"; break }

                # Prefer already-extracted tree (innounp / prior run)
                $srcCandidates = @(
                    (Join-Path $vendor "_tess_extract"),
                    (Join-Path $vendor "tesseract")
                ) | Where-Object { Test-Path (Join-Path $_ "tesseract.exe") }

                if ($srcCandidates) {
                    $src = $srcCandidates[0]
                    $dst = Join-Path $bin "tesseract"
                    Say "  copying portable Tesseract from $src ..."
                    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
                    Copy-Item $src $dst -Recurse -Force
                    # drop Inno junk
                    Remove-Item (Join-Path $dst '$PLUGINSDIR') -Recurse -Force -ErrorAction SilentlyContinue
                    Remove-Item (Join-Path $dst 'tesseract-uninstall.exe') -Force -ErrorAction SilentlyContinue
                } else {
                    $setup = Find-VendorFile "tesseract*.exe"
                    if (-not $setup) { Warn "tesseract: put installer in vendor\ and extract with innounp, or provide _tess_extract\"; break }
                    Warn "tesseract: Inno Setup needs admin OR pre-extract with innounp into vendor\_tess_extract"
                    Warn "  found installer: $setup — not running it (no admin). Extract offline then re-run."
                    break
                }

                # language packs
                $td = Join-Path $bin "tesseract\tessdata"
                Ensure-Dir $td
                foreach ($lang in @("chi_sim.traineddata", "eng.traineddata")) {
                    $dstLang = Join-Path $td $lang
                    if (Test-Path $dstLang) { continue }
                    $srcLang = @(
                        (Join-Path $vendor "tessdata\$lang"),
                        (Join-Path $vendor "_saved_tessdata\$lang")
                    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
                    if ($srcLang) { Copy-Item $srcLang $dstLang -Force }
                }
                if (Test-Path $exe) { Ok "Tesseract portable: $exe" }
                else { Bad "Tesseract copy failed" }
            }
            "ffmpeg" {
                $exe = Join-Path $bin "ffmpeg\bin\ffmpeg.exe"
                if (Test-Path $exe) { Ok "ffmpeg already in bin: $exe"; break }

                $zip = Find-VendorFile "ffmpeg*.zip"
                if (-not $zip) { Warn "ffmpeg: put ffmpeg*-essentials_build.zip into vendor\"; break }
                $tmp = Join-Path $bin "_ffmpeg_tmp"
                if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
                Ensure-Dir $tmp
                Say "  unpacking ffmpeg zip ..."
                Expand-Archive $zip -DestinationPath $tmp -Force
                $found = Get-ChildItem $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
                if (-not $found) { Bad "ffmpeg.exe not found inside zip"; break }
                $dst = Join-Path $bin "ffmpeg"
                if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
                Ensure-Dir $dst
                # essentials zip layout: ffmpeg-*-essentials_build/{bin,doc,presets}
                $inner = $found.Directory.Parent.FullName
                Move-Item (Join-Path $inner "*") $dst -Force
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                if (Test-Path $exe) { Ok "ffmpeg unpacked: $exe" }
                else { Bad "ffmpeg layout unexpected after unpack" }
            }
            default { Warn "unknown engine: $e" }
        }
    }
}

# ---------------------------------------------------------------- verify
Head "4. Verification"
& $py (Join-Path $root "docconvert.py") doctor
Say ""
& $py (Join-Path $root "tests\selftest.py")
Say ""
Say "Done. Quick start:" "White"
Say "  $py docconvert.py convert input.docx --to pdf -o out\"
Say "  $py docconvert.py doctor"
Say "  Copy the whole folder (incl. bin\) to another PC — no reinstall needed."
