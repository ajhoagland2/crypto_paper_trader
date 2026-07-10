$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "C:\Users\hoagl\Documents\Crypto_trading"
try {
    & "C:\Users\hoagl\AppData\Local\Programs\Python\Python39\python.exe" -u -m src.web_server *> web_server.out.log
} catch {
    $_ | Out-File -FilePath web_server.err.log -Encoding utf8
    throw
}
