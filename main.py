import json
import sys

import oci
import subprocess
import time
import chardet
import datetime
import requests

config = oci.config.from_file()
is_not_connection = True
latest_ip = None


def get_latest_public_ip():
    identity_client = oci.core.VirtualNetworkClient(config)
    compartment_id = config['tenancy']
    public_ips = identity_client.list_public_ips(scope='REGION', compartment_id=compartment_id).data
    # 筛选出预留的公共 IP
    reserved_ips = [public_ip for public_ip in public_ips if public_ip.lifecycle_state == 'ASSIGNED']
    # 按照创建时间排序，选择最新的 IP
    sorted_ips = sorted(reserved_ips, key=lambda ip: ip.time_created)
    if len(sorted_ips) == 0:
        return None
    return sorted_ips[-1]


def create_public_ip():
    compartment_id = config['tenancy']
    core_client = oci.core.VirtualNetworkClient(config)
    create_public_ip_response = core_client.create_public_ip(
        create_public_ip_details=oci.core.models.CreatePublicIpDetails(
            compartment_id=compartment_id,
            lifetime="RESERVED",
            display_name=f'nanh-public-ip-{datetime.datetime.now().strftime("%Y%m%d%H%M%S")}',
            private_ip_id=config['private_ip_id']))
    return create_public_ip_response.data


def delete_public_ip(public_ip_id):
    core_client = oci.core.VirtualNetworkClient(config)
    core_client.delete_public_ip(public_ip_id=public_ip_id)


def ping_ip(ip, timeout):
    p = subprocess.Popen(["ping", ip], stdout=subprocess.PIPE)
    # 将命令输出结果解码为字符串
    output = p.communicate()[0]
    encoding = chardet.detect(output)["encoding"]
    output = output.decode(encoding)

    if p.returncode == 0:
        return True
    else:
        time.sleep(timeout)
        return False


def get_dns_record(zone_id: str, api_key: str, dns_name: str) -> str:
    url = f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={dns_name}'

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()['result']
        if len(data) > 0:
            return data[0]
    return None


def update_dns_record_ip(zone_id: str, api_key: str, record, new_ip: str) -> bool:
    record_id = record['id']
    url = f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}'

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }

    data = {
        'type': 'A',
        'name': record['name'],
        'content': new_ip,
        'proxied': record['proxied']
    }

    response = requests.put(url, headers=headers, json=data)

    if response.status_code == 200:
        return True
    else:
        return False


if __name__ == '__main__':
    while is_not_connection:
        try:
            if latest_ip is None:
                latest_ip = get_latest_public_ip()
            if latest_ip is None:
                latest_ip = create_public_ip()
            ping_status = ping_ip(latest_ip.ip_address, 10)
            is_not_connection = not ping_status
            if ping_status:
                print(f'连接IP: {latest_ip.ip_address}, 成功')
            else:
                print(f'连接IP: {latest_ip.ip_address}, 失败')
                delete_public_ip(latest_ip.id)
                time.sleep(2)
                latest_ip = create_public_ip()
        except Exception as e:
            latest_ip = None
            time.sleep(30)
            pass
    print(f"更新IP成功，最新IP: {latest_ip.ip_address}")
    dns_names = config['cloudflare_dns_names']
    dns_name_list = dns_names.split(' ')
    for name in dns_name_list:
        record = get_dns_record(config['cloudflare_zone_id'], config['cloudflare_api_key'], name)
        if update_dns_record_ip(config['cloudflare_zone_id'], config['cloudflare_api_key'], record, latest_ip.ip_address):
            print(f'成功将 DNS 记录 {name} 的 IP 修改为 {latest_ip.ip_address}')
        else:
            print(f'修改 DNS 记录 {name} 的 IP 失败')
