param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$true)][string]$PublisherThumbprint
)
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $File
if ($signature.Status -ne 'Valid') {
    throw "The release artifact does not have a valid Authenticode signature: $($signature.Status)"
}
if ($signature.SignerCertificate.Thumbprint -ne $PublisherThumbprint) {
    throw 'The release artifact was not signed by the expected publisher certificate.'
}
Write-Output 'Publisher signature verified.'
