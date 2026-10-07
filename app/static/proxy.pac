// proxy.pac — browser auto-config for flexchat.top users in sanctioned regions.
//
// Routes browser traffic through a LOCAL SOCKS5 tunnel (bc-proxy → Turkey
// server 166.1.2.48) except for:
//   - plain hostnames / localhost / LAN / private ranges (direct)
//   - Russian domains: *.ru, *.su, *.рф (xn--p1ai) — direct
//   - flexchat.top and the project servers — direct (always reachable)
//
// If the local tunnel is not running, requests fall back to DIRECT, so
// browsing never breaks — sanctioned destinations will simply fail from the
// local (RU) IP as they would without the proxy.
//
// Install: browser proxy auto-config URL → https://flexchat.top/proxy.pac
// Start the tunnel first:  ~/bin/bc-proxy start

function isPrivateIp(ip) {
  return isInNet(ip, "10.0.0.0", "255.0.0.0") ||
         isInNet(ip, "172.16.0.0", "255.240.0.0") ||
         isInNet(ip, "192.168.0.0", "255.255.0.0") ||
         isInNet(ip, "127.0.0.0", "255.0.0.0") ||
         isInNet(ip, "169.254.0.0", "255.255.0.0") ||
         isInNet(ip, "100.64.0.0", "255.192.0.0");
}

function FindProxyForURL(url, host) {
  // Never proxy the project's own infrastructure.
  if (dnsDomainIs(host, "flexchat.top") ||
      host === "166.1.2.48" || host === "62.109.10.170") {
    return "DIRECT";
  }
  // Local / LAN.
  if (isPlainHostName(host) ||
      shExpMatch(host, "localhost") ||
      shExpMatch(host, "*.local")) {
    return "DIRECT";
  }
  // Russian TLDs stay direct (no sanctions on .ru/.su/.рф origins).
  if (shExpMatch(host, "*.ru") || shExpMatch(host, "*.su") ||
      shExpMatch(host, "*.xn--p1ai") || shExpMatch(host, "xn--p1ai")) {
    return "DIRECT";
  }
  // Google sign-in works fine from RU IPs and is much faster direct;
  // the OAuth popup no longer round-trips through the tunnel.
  if (dnsDomainIs(host, "accounts.google.com") ||
      dnsDomainIs(host, "accounts.youtube.com") ||
      dnsDomainIs(host, "myaccount.google.com")) {
    return "DIRECT";
  }
  // Private/reserved IP targets stay direct.
  var ip = dnsResolve(host);
  if (ip && isPrivateIp(ip)) {
    return "DIRECT";
  }
  // Everything else egresses via the Turkey tunnel (remote DNS: hostname is
  // passed to the SOCKS5 proxy, so no local DNS leak).
  return "SOCKS5 127.0.0.1:1080; DIRECT";
}
