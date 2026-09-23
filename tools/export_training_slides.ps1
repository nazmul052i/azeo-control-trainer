param([Parameter(Mandatory = $true)][string]$SpecPath)
$ErrorActionPreference = 'Stop'
$deckSpec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
$renderDir = Join-Path $deckSpec.buildDir 'render'
New-Item -ItemType Directory -Path $renderDir -Force | Out-Null
$candidatePath = Join-Path $deckSpec.buildDir 'candidate.pptx'
$geometry = [System.Collections.Generic.List[object]]::new()
$nativeObjects = [System.Collections.Generic.List[object]]::new()
$notesCount = 0

function RGB([string]$hex) {
    $hex = $hex.TrimStart('#')
    return [Convert]::ToInt32($hex.Substring(0, 2), 16) +
      256 * [Convert]::ToInt32($hex.Substring(2, 2), 16) +
      65536 * [Convert]::ToInt32($hex.Substring(4, 2), 16)
}
$blue = RGB '004487'
$navy = RGB '102C49'
$ink = RGB '263746'
$muted = RGB '586A79'
$white = RGB 'FFFFFF'
$pale = RGB 'EFF5FA'

function Add-Text($slide, [string]$name, [string]$text, [double]$x, [double]$y,
    [double]$w, [double]$h, [double]$size, [int]$color, [bool]$bold = $false) {
    $box = $slide.Shapes.AddTextbox(1, $x, $y, $w, $h)
    $box.Name = $name
    $box.TextFrame.WordWrap = -1
    $box.TextFrame.AutoSize = 0
    $box.TextFrame.MarginLeft = 0
    $box.TextFrame.MarginRight = 0
    $box.TextFrame.MarginTop = 0
    $box.TextFrame.MarginBottom = 0
    $box.TextFrame.TextRange.Text = $text
    $box.TextFrame.TextRange.Font.Name = $deckSpec.font
    $box.TextFrame.TextRange.Font.Size = $size
    $box.TextFrame.TextRange.Font.Color.RGB = $color
    $box.TextFrame.TextRange.Font.Bold = if ($bold) { -1 } else { 0 }
    $box.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 0
    $box.TextFrame.TextRange.ParagraphFormat.SpaceBefore = 0
    # Changing the textbox AutoSize resets its height to the Office default.
    # Restore authored geometry after formatting, before checking text fit.
    $box.Left = $x
    $box.Top = $y
    $box.Width = $w
    $box.Height = $h
    return $box
}

function Add-Lines($slide, [string]$name, $lines, [double]$x, [double]$y,
    [double]$w, [double]$h, [double]$size = 23, [int]$color = $ink) {
    $box = Add-Text $slide $name ($lines -join "`r") $x $y $w $h $size $color
    $box.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 22
    return $box
}

function Add-Table($slide, $spec) {
    $columnCount = $spec.columns.Count
    $rowCount = $spec.rows.Count + 1
    $fontSize = if ($rowCount -ge 8) { 17 } else { 18 }
    $widths = switch ($columnCount) {
        2 { @(0.30, 0.70) }
        3 { @(0.23, 0.385, 0.385) }
        4 { @(0.23, 0.23, 0.23, 0.31) }
        default { throw "Unsupported table columns: $columnCount" }
    }
    if ($spec.title -in @('Course assessment','Capstone rubric','Capstone time plan')) { $widths = @(0.77, 0.23) }
    $tableShape = $slide.Shapes.AddTable($rowCount, $columnCount, 44, 132, 872, 310)
    $tableShape.Name = 'EvidenceTable'
    $tab = $tableShape.Table
    for ($c = 1; $c -le $columnCount; $c++) { $tab.Columns.Item($c).Width = 872 * $widths[$c - 1] }
    for ($r = 1; $r -le $rowCount; $r++) {
        for ($c = 1; $c -le $columnCount; $c++) {
            $cell = $tab.Cell($r, $c)
            $shape = $cell.Shape
            $shape.TextFrame.MarginLeft = 10
            $shape.TextFrame.MarginRight = 10
            $shape.TextFrame.MarginTop = 6
            $shape.TextFrame.MarginBottom = 6
            # PowerPoint table cells wrap intrinsically and reject WordWrap writes.
            $shape.TextFrame.VerticalAnchor = 3
            $shape.TextFrame.TextRange.Text = if ($r -eq 1) { [string]$spec.columns[$c - 1] } else { [string]$spec.rows[$r - 2][$c - 1] }
            $shape.TextFrame.TextRange.Font.Name = $deckSpec.font
            $shape.TextFrame.TextRange.Font.Size = $fontSize
            $shape.TextFrame.TextRange.Font.Bold = if ($r -eq 1) { -1 } else { 0 }
            $shape.TextFrame.TextRange.Font.Color.RGB = if ($r -eq 1) { $white } else { $ink }
            $shape.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 0
            $shape.Fill.ForeColor.RGB = if ($r -eq 1) { $blue } elseif ($r % 2 -eq 0) { $pale } else { $white }
            for ($b = 1; $b -le 4; $b++) { $cell.Borders.Item($b).ForeColor.RGB = $white; $cell.Borders.Item($b).Weight = 0.6 }
        }
        $neededHeight = 36.0
        for ($c = 1; $c -le $columnCount; $c++) {
            $neededHeight = [Math]::Max($neededHeight, $tab.Cell($r, $c).Shape.TextFrame.TextRange.BoundHeight + 16)
        }
        $tab.Rows.Item($r).Height = $neededHeight
    }
    $bottom = $tableShape.Top + $tableShape.Height
    if ($spec.note) { $null = Add-Text $slide 'EvidenceNote' $spec.note 44 ([Math]::Max(452, $bottom + 14)) 872 44 17 $muted }
    $nativeObjects.Add(@{ slide = $slide.SlideIndex; kind = 'table'; rows = $rowCount; columns = $columnCount })
}

function Add-Picture($slide, $spec) {
    $imagePath = Join-Path $deckSpec.sourceRoot $spec.image
    if (-not (Test-Path -LiteralPath $imagePath)) { throw "Missing image: $imagePath" }
    $picture = $slide.Shapes.AddPicture($imagePath, 0, -1, 44, 123, -1, -1)
    $picture.Name = 'ProductReferenceImage'
    $picture.LockAspectRatio = -1
    $availableWidth = if ($spec.side) { 270.0 } else { 872.0 }
    $availableHeight = 345.0
    $scale = [Math]::Min($availableWidth / $picture.Width, $availableHeight / $picture.Height)
    $picture.Width *= $scale
    $picture.Left = 44 + ($availableWidth - $picture.Width) / 2
    $picture.Top = 123 + ($availableHeight - $picture.Height) / 2
    if ($spec.side) { $null = Add-Lines $slide 'ImageDiscussion' $spec.side 370 155 520 290 25 }
    $null = Add-Text $slide 'ImageCaption' $spec.caption 44 479 872 35 17 $muted
}

function Add-Chart($slide, $spec) {
    # Import a native chart with a complete embedded workbook. Activating Excel
    # during an unattended export can leave Office waiting on its chart editor.
    $originalIndex = $slide.SlideIndex
    $chartPath = Join-Path $deckSpec.sourceRoot 'tmp\training_slides\native_chart.pptx'
    [void]$presentation.Slides.InsertFromFile($chartPath, $originalIndex, 1, 1)
    $slide.Delete()
    $imported = $presentation.Slides.Item($originalIndex)
    $imported.Shapes.Item('CourseFooter').Delete()
    $imported.Shapes.Item('SlideNumber').Delete()
    $nativeObjects.Add(@{ slide = $originalIndex; kind = 'chart'; series = $spec.series.Count })
    return $imported
}

function Inspect-Slide($slide) {
    foreach ($shape in $slide.Shapes) {
        if ($shape.Left -lt -1 -or $shape.Top -lt -1 -or ($shape.Left + $shape.Width) -gt 961 -or ($shape.Top + $shape.Height) -gt 541) {
            $geometry.Add(@{slide=$slide.SlideIndex; shape=$shape.Name; issue='outside slide'; bounds=@($shape.Left,$shape.Top,$shape.Width,$shape.Height)})
        }
        if ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
            $textFrame = $shape.TextFrame
            if ($textFrame.TextRange.BoundHeight -gt $shape.Height + 1) {
                $geometry.Add(@{slide=$slide.SlideIndex; shape=$shape.Name; issue='text overflow'; needed=$textFrame.TextRange.BoundHeight; available=$shape.Height})
            }
        }
    }
}

$powerPoint = New-Object -ComObject PowerPoint.Application
$powerPoint.DisplayAlerts = 1
$presentation = $null
try {
    $presentation = $powerPoint.Presentations.Add(0)
    $presentation.PageSetup.SlideWidth = $deckSpec.width
    $presentation.PageSetup.SlideHeight = $deckSpec.height
    $index = 0
    foreach ($spec in $deckSpec.slides) {
        $index++
        if ($index -le 5 -or $spec.type -eq 'chart') { Write-Output "Building slide $index ($($spec.type))" }
        $slide = $presentation.Slides.Add($index, 12)
        $slide.Name = 'ACT_' + $index.ToString('000')
        $slide.FollowMasterBackground = 0
        $slide.Background.Fill.Solid()
        $slide.Background.Fill.ForeColor.RGB = $white
        if ($spec.type -in @('cover','section')) {
            $slide.Background.Fill.ForeColor.RGB = if ($spec.type -eq 'cover') { $navy } else { $blue }
            $null = Add-Text $slide 'Title' $spec.title 52 115 856 168 48 $white $true
            $null = Add-Text $slide 'Subtitle' $spec.subtitle 52 301 856 80 29 $white
            $null = Add-Text $slide 'SectionDetail' ($spec.lines -join "`r") 52 415 856 87 19 $white
        } else {
            $null = Add-Text $slide 'Title' $spec.title 44 35 872 82 34 $blue $true
            switch ($spec.type) {
                'text' { $null = Add-Lines $slide 'Body' $spec.lines 52 151 856 329 24 }
                'session' {
                    $null = Add-Lines $slide 'Outcomes' $spec.lines 52 151 856 258 25
                    $null = Add-Text $slide 'SessionTiming' $spec.timing 52 437 856 56 19 $muted
                }
                'compare' {
                    $null = Add-Text $slide 'LeftHeading' $spec.leftTitle 44 139 415 61 25 $ink $true
                    $null = Add-Text $slide 'RightHeading' $spec.rightTitle 505 139 411 61 25 $ink $true
                    $null = Add-Lines $slide 'LeftBody' $spec.left 44 226 415 253 22
                    $null = Add-Lines $slide 'RightBody' $spec.right 505 226 411 253 22
                }
                'table' { Add-Table $slide $spec }
                'image' { Add-Picture $slide $spec }
                'chart' { $slide = Add-Chart $slide $spec }
                'questions' { $null = Add-Lines $slide 'Questions' $spec.lines 52 155 856 329 26 }
                'lab' {
                    $null = Add-Text $slide 'LabSetup' $spec.setup 52 130 856 72 20 $muted
                    $steps = for ($n = 0; $n -lt $spec.lines.Count; $n++) { ($n + 1).ToString() + '.  ' + $spec.lines[$n] }
                    $null = Add-Lines $slide 'LabSteps' $steps 52 225 856 248 22
                    $null = Add-Text $slide 'LabDuration' ($spec.duration.ToString() + ' min practical') 52 483 800 26 17 $blue
                }
                'debrief' {
                    $null = Add-Text $slide 'EvidenceHeading' 'Evidence' 52 137 856 31 22 $blue $true
                    $null = Add-Text $slide 'Evidence' $spec.evidence 52 174 856 52 22 $ink
                    $null = Add-Text $slide 'AcceptanceHeading' 'Acceptance and restoration' 52 240 856 31 22 $blue $true
                    $null = Add-Text $slide 'Acceptance' ($spec.pass + "`r" + $spec.cleanup) 52 277 856 105 22 $ink
                    $null = Add-Text $slide 'DebriefQuestion' $spec.prompt 52 416 856 78 23 $blue
                }
                default { throw "Unknown slide type $($spec.type)" }
            }
        }
        $footerColor = if ($spec.type -in @('cover','section')) { $white } else { $muted }
        if ($spec.type -notin @('cover','section')) {
            $footerText = if ($spec.day) { 'Day ' + $spec.day + ' / ' + $spec.session } else { 'ACT-FND-01' }
            $null = Add-Text $slide 'CourseFooter' $footerText 44 518 800 18 12 $footerColor
        }
        $null = Add-Text $slide 'SlideNumber' $index.ToString() 903 518 40 18 12 $footerColor
        $slide.Name = 'ACT_' + $index.ToString('000')
        $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = $spec.notes
        $notesCount++
        Inspect-Slide $slide
        if ($index % 10 -eq 0) { Write-Output "Built slide $index / $($deckSpec.slides.Count)" }
        if ($index % 20 -eq 0) { $presentation.SaveAs($candidatePath, 24) }
    }
    # Native sections let an instructor jump between days without adding UI-like controls.
    $sectionStart = 1
    $sectionName = 'Course introduction'
    for ($i = 0; $i -lt $deckSpec.slides.Count; $i++) {
        if ($deckSpec.slides[$i].type -eq 'section') {
            [void]$presentation.SectionProperties.AddBeforeSlide($sectionStart, $sectionName)
            $sectionStart = $i + 1
            $sectionName = if ($deckSpec.slides[$i].day) { 'Day ' + $deckSpec.slides[$i].day } else { 'Instructor appendix' }
        }
    }
    [void]$presentation.SectionProperties.AddBeforeSlide($sectionStart, $sectionName)
    $presentation.SaveAs($candidatePath, 24)
    for ($i = 1; $i -le $presentation.Slides.Count; $i++) {
        $presentation.Slides.Item($i).Export((Join-Path $renderDir ('slide-' + $i.ToString('000') + '.png')), 'PNG', 1280, 720)
        if ($i % 20 -eq 0) { Write-Output "Rendered slide $i / $($presentation.Slides.Count)" }
    }
    $report = @{ slides=$presentation.Slides.Count; notes=$notesCount; geometry=@($geometry.ToArray()); native_objects=@($nativeObjects.ToArray()); candidate=$candidatePath; renders=$renderDir; renderer='Installed Microsoft PowerPoint'; artifact_tool_runtime='Unavailable in this session' }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $deckSpec.buildDir 'powerpoint_audit.json') -Encoding UTF8
    Write-Output ('PowerPoint export complete. Geometry issues: ' + $geometry.Count)
    if ($geometry.Count -gt 0) { $geometry | ConvertTo-Json -Depth 5; exit 2 }
} catch {
    if ($null -ne $presentation -and $presentation.Slides.Count -gt 0) {
        $presentation.SaveAs((Join-Path $deckSpec.buildDir 'partial.pptx'), 24)
    }
    throw
} finally {
    if ($null -ne $presentation) { $presentation.Close() }
    if ($powerPoint.Presentations.Count -eq 0) { $powerPoint.Quit() }
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint)
}
