"""Windows physical/private IPv4 interface and narrowly scoped firewall policy."""
import base64
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
import subprocess
import sys

PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ipv4(value):
    try:
        addr = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(addr in net for net in PRIVATE)


@dataclass(frozen=True)
class Interface:
    ip: str
    prefix: int
    index: int
    name: str
    category: str
    firewall: bool

    @property
    def network(self):
        return ipaddress.IPv4Network(f"{self.ip}/{self.prefix}", strict=False)

    def contains(self, address):
        try:
            peer = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            return False
        return (private_ipv4(address) and peer in self.network and
                peer not in (self.network.network_address, self.network.broadcast_address))

    def validate(self):
        if (not private_ipv4(self.ip) or not 8 <= self.prefix <= 30 or
                not any(self.network.subnet_of(n) for n in PRIVATE)):
            raise ValueError("请选择具有有效私有 IPv4 子网的本地网卡")
        if self.category != "Private":
            raise ValueError("此网卡是公用网络。请在 Windows 网络设置中将可信家庭/教室网络设为“专用”，然后刷新。")
        if not self.firewall:
            raise ValueError("Windows 专用网络防火墙未开启，请先开启防火墙")


def powershell(script, timeout=20):
    if os.name != "nt":
        raise RuntimeError("此版本的局域网入口限制支持 Windows")
    # EncodedCommand otherwise serializes module progress/errors as CLIXML.
    # An intentionally missing firewall rule must not inherit exit code 1
    # from Get-NetFirewallRule -ErrorAction SilentlyContinue.
    wrapped = """$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
try {
""" + script + """
exit 0
} catch {
[Console]::Error.WriteLine($_.Exception.Message)
exit 1
}
"""
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-EncodedCommand", encoded], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("网络配置失败：" + (result.stderr.strip() or result.stdout.strip())[:700])
    return result.stdout.strip().lstrip("\ufeff")


def interfaces():
    raw = powershell("""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$fw=(Get-NetFirewallProfile -Name Private -PolicyStore ActiveStore).Enabled
$rows=@(Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object {
  $a=$_
  $nic=Get-NetAdapter -InterfaceIndex $a.InterfaceIndex -ErrorAction SilentlyContinue
  $profile=Get-NetConnectionProfile -InterfaceIndex $a.InterfaceIndex -ErrorAction SilentlyContinue
  if($nic.HardwareInterface -and $nic.Status -eq 'Up' -and $a.AddressState -eq 'Preferred') {
    [pscustomobject]@{ip=$a.IPAddress;prefix=[int]$a.PrefixLength;index=[int]$a.InterfaceIndex;
      name=$a.InterfaceAlias;category=[string]$profile.NetworkCategory;firewall=[bool]$fw}
  }
})
ConvertTo-Json -InputObject $rows -Compress
""")
    rows = json.loads(raw or "[]")
    return [Interface(**r) for r in rows if private_ipv4(r["ip"])]


def current_interface(ip):
    for item in interfaces():
        if item.ip == ip:
            item.validate()
            return item
    raise ValueError("所选本地网卡已断开、地址已变化或不是物理网卡")


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def ensure_firewall(interface, port):
    """Called by Create Room only. UAC authorizes a persistent, narrow rule.

    No network-profile changes, router mappings, broad Python allow rules or
    global firewall changes. A closed room has no listener even if rule remains.
    """
    live = current_interface(interface.ip)
    if live != interface:
        raise ValueError("网络配置已变化，请刷新网卡列表")
    if not 1024 <= port <= 65535:
        raise ValueError("房间端口必须在 1024–65535 范围")
    program = os.path.realpath(sys.executable)
    digest = hashlib.sha256(f"{program}|{interface.ip}|{interface.network}|{port}".encode()).hexdigest()[:16]
    name = "XiangqiCoach-LAN-" + digest
    script = f"""$ErrorActionPreference='Stop'
$ip=Get-NetIPAddress -AddressFamily IPv4 -IPAddress {ps_quote(interface.ip)}
$profile=Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex
if($profile.NetworkCategory -ne 'Private') {{ throw 'Only Private network is allowed' }}
if(-not (Get-NetFirewallProfile -Name Private -PolicyStore ActiveStore).Enabled) {{ throw 'Firewall must be enabled' }}
$existing=Get-NetFirewallRule -Name {ps_quote(name)} -ErrorAction SilentlyContinue
if($existing) {{ Remove-NetFirewallRule -Name {ps_quote(name)} }}
New-NetFirewallRule -Name {ps_quote(name)} -DisplayName 'Xiangqi Coach LAN (private subnet only)' `
 -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP `
 -LocalPort {port} -LocalAddress {ps_quote(interface.ip)} -RemoteAddress {ps_quote(str(interface.network))} `
 -InterfaceAlias {ps_quote(interface.name)} -Program {ps_quote(program)} -EdgeTraversalPolicy Block | Out-Null
"""
    # Check for the exact rule first so repeat rooms normally need no UAC.
    check = f"""$r=Get-NetFirewallRule -PolicyStore ActiveStore -Name {ps_quote(name)} -ErrorAction SilentlyContinue
if($r -and $r.Enabled -eq 'True' -and $r.Action -eq 'Allow' -and $r.Direction -eq 'Inbound' -and [string]$r.Profile -eq 'Private' -and [string]$r.EdgeTraversalPolicy -eq 'Block') {{
 $a=$r | Get-NetFirewallAddressFilter
 $p=$r | Get-NetFirewallPortFilter
 $e=$r | Get-NetFirewallApplicationFilter
 $i=$r | Get-NetFirewallInterfaceFilter
 # Windows may return /255.255.255.0 instead of /24 for the same subnet.
 $remoteMatches=(@($a.RemoteAddress).Count -eq 1 -and (@({ps_quote(str(interface.network))}, {ps_quote(interface.network.with_netmask)}) -contains [string](@($a.RemoteAddress)[0])))
 if(@($a.LocalAddress).Count -eq 1 -and $a.LocalAddress -eq {ps_quote(interface.ip)} -and $remoteMatches -and $p.LocalPort -eq '{port}' -and $p.Protocol -eq 'TCP' -and $e.Program -eq {ps_quote(program)} -and @($i.InterfaceAlias).Count -eq 1 -and $i.InterfaceAlias -eq {ps_quote(interface.name)}) {{ 'OK' }}
}}
"""
    if powershell(check) == "OK":
        return name
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell(f"""$ErrorActionPreference='Stop'
$p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile -NonInteractive -EncodedCommand {encoded}'
if($p.ExitCode -ne 0) {{ throw 'Firewall setup was not completed' }}
""", timeout=120)
    if powershell(check) != "OK":
        raise RuntimeError("未能确认受限防火墙规则生效，房间未开放")
    return name
