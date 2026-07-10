param(
    [string]$Symbol = "BTC-USD",
    [int]$Minutes = 30,
    [string]$OutputPath = "C:\tmp\paper_review.jsonl",
    [string]$DonePath = "C:\tmp\paper_review.done",
    [double]$EntryThreshold = 50,
    [double]$ExitThreshold = 40,
    [double]$TargetProfitPct = 3,
    [double]$PaperAllocationPerTrade = 1,
    [double]$CatastrophicLossPct = -3
)

$ErrorActionPreference = "Continue"
$normalizedSymbol = $Symbol.Trim().ToUpperInvariant()
if ($normalizedSymbol -notlike "*-*") {
    $normalizedSymbol = "$normalizedSymbol-USD"
}

Remove-Item -LiteralPath $OutputPath, $DonePath -ErrorAction SilentlyContinue
$end = (Get-Date).AddMinutes($Minutes)
$body = @{
    symbol = $normalizedSymbol
    momentumEntryThreshold = $EntryThreshold
    momentumExitThreshold = $ExitThreshold
    targetProfitPct = $TargetProfitPct
    paperAllocationPerTrade = $PaperAllocationPerTrade
    catastrophicLossPct = $CatastrophicLossPct
} | ConvertTo-Json

while ((Get-Date) -lt $end) {
    try {
        $response = Invoke-RestMethod `
            -Uri "http://localhost:8766/api/live-paper/step" `
            -Method Post `
            -Body $body `
            -ContentType "application/json" `
            -TimeoutSec 20
        $status = $response.status
        $momentum = $status.momentum.$normalizedSymbol
        $position = @($status.positions | Where-Object { $_.symbol -eq $normalizedSymbol } | Select-Object -First 1)
        $events = @($status.eventHistory | Select-Object -Last 10)
        $latest = $events | Where-Object { $_.symbol -eq $normalizedSymbol } | Select-Object -Last 1
        $row = [pscustomobject]@{
            ts = (Get-Date).ToUniversalTime().ToString("o")
            runId = $status.simulationRunId
            symbol = $normalizedSymbol
            price = $status.latestPrices.$normalizedSymbol
            signal = if ($latest) { $latest.signal } else { $null }
            eventType = if ($latest) { $latest.type } else { $null }
            details = if ($latest) { $latest.details } else { $null }
            score = if ($momentum) { $momentum.score } else { $null }
            currentReturn = if ($momentum) { $momentum.stats.current_return } else { $null }
            zScore = if ($momentum) { $momentum.stats.z_score } else { $null }
            trades = $status.trades
            positions = @($status.positions).Count
            hasPosition = [bool]$position
            quantity = if ($position) { $position.quantity } else { 0 }
            averagePrice = if ($position) { $position.average_price } else { 0 }
            cash = $status.cash
            availableCash = $status.availableCash
            portfolioValue = $status.portfolioValue
            realizedPl = $status.realizedPl
            unrealizedPl = $status.unrealizedPl
            gainReserve = $status.gainReserve
            latestError = $status.lastError
        }
        $row | ConvertTo-Json -Compress -Depth 6 | Add-Content -LiteralPath $OutputPath
    } catch {
        [pscustomobject]@{
            ts = (Get-Date).ToUniversalTime().ToString("o")
            symbol = $normalizedSymbol
            error = $_.Exception.Message
        } | ConvertTo-Json -Compress | Add-Content -LiteralPath $OutputPath
    }
    Start-Sleep -Seconds 1
}

"DONE" | Set-Content -LiteralPath $DonePath
