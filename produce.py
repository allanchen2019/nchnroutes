#!/usr/bin/env python3
import argparse
import csv
from ipaddress import IPv4Network, IPv6Network
import math
import subprocess
import urllib.request

parser = argparse.ArgumentParser(description='Generate non-China routes for BIRD.')
parser.add_argument('--exclude', metavar='CIDR', type=str, nargs='*',
                    help='IPv4 ranges to exclude in CIDR format')
parser.add_argument('--next', default="wg0", metavar = "INTERFACE OR IP",
                    help='next hop for where non-China IP address, this is usually the tunnel interface')
parser.add_argument('--ipv4-list', choices=['apnic', 'ipip'], default=['apnic', 'ipip'], nargs='*',
                    help='IPv4 lists to use when subtracting China based IP, multiple lists can be used at the same time (default: apnic ipip)')
parser.add_argument('--bird', action='store_true', help='Apply routes to bird (move conf and reload)')
parser.add_argument('--no-git', action='store_true', help='Skip git pull')
args = parser.parse_args()

def run_git_pull():
    if args.no_git:
        print("[SKIP] git pull")
        return
    print("[RUN] git pull")
    subprocess.run(["git", "pull"])

def download_file(url, filename):
    print(f"[DOWNLOAD] {filename} from {url}")
    try:
        urllib.request.urlretrieve(url, filename)
    except Exception as e:
        print(f"下载 {filename} 失败: {e}")

def main_makefile_steps():
    run_git_pull()
    download_file(
        "https://ftp.apnic.net/stats/apnic/delegated-apnic-latest",
        "delegated-apnic-latest"
    )
    download_file(
        "https://raw.githubusercontent.com/17mon/china_ip_list/master/china_ip_list.txt",
        "china_ip_list.txt"
    )

main_makefile_steps()

class Node:
    __slots__ = ('cidr', 'child', 'dead', 'parent')
    def __init__(self, cidr, parent=None):
        self.cidr = cidr
        self.child = []
        self.dead = False
        self.parent = parent

    def __repr__(self):
        return f"<Node {self.cidr}>"

def dump_tree(lst, ident=0):
    for n in lst:
        print("+" * ident + str(n))
        dump_tree(n.child, ident + 1)

def dump_bird(lst, f):
    for n in lst:
        if n.dead:
            continue
        if n.child:
            dump_bird(n.child, f)
        else:
            f.write(f'route {n.cidr} via "{args.next}";\n')

RESERVED = {
    IPv4Network('0.0.0.0/8'),
    IPv4Network('10.0.0.0/8'),
    IPv4Network('127.0.0.0/8'),
    IPv4Network('169.254.0.0/16'),
    IPv4Network('172.16.0.0/12'),
    IPv4Network('192.0.0.0/29'),
    IPv4Network('192.0.0.170/31'),
    IPv4Network('192.0.2.0/24'),
    IPv4Network('192.168.0.0/16'),
    IPv4Network('198.18.0.0/15'),
    IPv4Network('198.51.100.0/24'),
    IPv4Network('203.0.113.0/24'),
    IPv4Network('240.0.0.0/4'),
    IPv4Network('255.255.255.255/32'),
    IPv4Network('224.0.0.0/4'),
    IPv4Network('100.64.0.0/10'),
}
RESERVED_V6 = []
if args.exclude:
    for e in args.exclude:
        if ":" in e:
            RESERVED_V6.append(IPv6Network(e))
        else:
            RESERVED.add(IPv4Network(e))

IPV6_UNICAST = IPv6Network('2000::/3')

def subtract_cidr(sub_from, sub_by):
    sub_by_set = set(sub_by)
    for n in sub_from:
        if n.dead:
            continue
        if n.cidr in sub_by_set:
            n.dead = True
            continue
        for cidr_to_sub in sub_by:
            if n.cidr.supernet_of(cidr_to_sub):
                if n.child:
                    subtract_cidr(n.child, [cidr_to_sub])
                else:
                    n.child = [Node(b, n) for b in n.cidr.address_exclude(cidr_to_sub)]
                break

root = []
root_v6 = [Node(IPV6_UNICAST)]

with open("ipv4-address-space.csv", newline='') as f:
    f.readline() # skip the title
    reader = csv.reader(f, quoting=csv.QUOTE_MINIMAL)
    for cidr in reader:
        if cidr[5] in ("ALLOCATED", "LEGACY"):
            block = cidr[0]
            cidr_str = f"{block[:3].lstrip('0')}.0.0.0{block[-2:]}"
            root.append(Node(IPv4Network(cidr_str)))

with open("delegated-apnic-latest") as f:
    for line in f:
        if 'apnic' in args.ipv4_list and "apnic|CN|ipv4|" in line:
            line = line.split("|")
            a = IPv4Network(f"{line[3]}/{int(32 - math.log(int(line[4]), 2))}")
            subtract_cidr(root, (a,))
        elif "apnic|CN|ipv6|" in line:
            line = line.split("|")
            a = IPv6Network(f"{line[3]}/{line[4]}")
            subtract_cidr(root_v6, (a,))

if 'ipip' in args.ipv4_list:
    with open("china_ip_list.txt") as f:
        for line in f:
            line = line.strip('\n')
            a = IPv4Network(line)
            subtract_cidr(root, (a,))

subtract_cidr(root, RESERVED)
subtract_cidr(root_v6, RESERVED_V6)

with open("routes4.conf", "w") as f:
    dump_bird(root, f)

with open("routes6.conf", "w") as f:
    dump_bird(root_v6, f)

def bird_config():
    print("[BIRD] 配置 bird 路由...")
    try:
        subprocess.run(["sudo", "mv", "routes4.conf", "/etc/bird/routes4.conf"])
        subprocess.run(["sudo", "mv", "routes6.conf", "/etc/bird/routes6.conf"])
        subprocess.run(["sudo", "birdc", "configure"])
        subprocess.run(["sudo", "birdc6", "configure"])
    except Exception as e:
        print(f"bird 配置失败: {e}")

if args.bird:
    bird_config()
