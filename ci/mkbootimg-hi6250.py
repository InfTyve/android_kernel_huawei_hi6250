#!/usr/bin/env python3
"""
mkbootimg-hi6250.py —— 用原厂 boot header 模板重新打包 hi6250 / LLD 的 kernel.img

背景：
  原厂 kernel.img 是 Android boot v0 布局、ramdisk 分离的镜像（ramdisk_size=0），
  kernel 分区固定 24 MiB。改内核只需要替换 kernel 段，header 里的
  kernel_addr / tags_addr / cmdline / os_version / page_size 必须原样保留，
  否则设备会卡在黑屏或 logo。

  因此本脚本不复刻 mkbootimg 的参数推导逻辑，而是直接把原厂 header
  （ci/bootheader-hi6250.bin，1632 字节）当模板，只改写 kernel_size 字段。

  header 尾部的 AVB / DTB 附加数据无法重签，统一补零；刷入时必须
      fastboot --disable-verification flash vbmeta <ven/vbmeta.img>

用法：
  python3 ci/mkbootimg-hi6250.py \
      --header ci/bootheader-hi6250.bin \
      --kernel out/arch/arm64/boot/Image.gz \
      --output dist/kernel-4.9.171-TeYun.img

依赖：仅 python3 标准库。
"""

import argparse
import hashlib
import os
import struct
import sys

# Android boot image header v0
HDR_FMT = '<8s10I16s512s32s1024s'
HDR_LEN = struct.calcsize(HDR_FMT)  # 1632
MAGIC = b'ANDROID!'
# hi6250 的 kernel 分区大小（fastboot getvar partition-size:kernel）
DEFAULT_PARTITION = 25165824


def pad_to(data, page):
    rem = len(data) % page
    return data + b'\0' * (page - rem) if rem else data


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description='用原厂 boot header 模板打包 hi6250 内核镜像')
    ap.add_argument('--header', required=True, help='1632 字节的原厂 boot header 模板')
    ap.add_argument('--kernel', required=True, help='新编译出的 Image.gz')
    ap.add_argument('--output', required=True, help='输出 .img 路径')
    ap.add_argument('--partition-size', type=int, default=DEFAULT_PARTITION,
                    help='kernel 分区大小，默认 %d' % DEFAULT_PARTITION)
    args = ap.parse_args()

    hdr_raw = open(args.header, 'rb').read()
    if len(hdr_raw) != HDR_LEN:
        sys.exit('header 模板应为 %d 字节，实际 %d' % (HDR_LEN, len(hdr_raw)))

    fields = list(struct.unpack(HDR_FMT, hdr_raw))
    (magic, ksize, kaddr, rsize, raddr,
     ssize, saddr, tags, page, hdrver, osver) = fields[:11]
    name, cmdline, board, extra = fields[11:15]

    if magic != MAGIC:
        sys.exit('header 模板 magic 不对: %r' % magic)
    if rsize != 0:
        sys.exit('模板含 ramdisk (%d 字节)，本脚本只适用于 ramdisk 分离的机型' % rsize)
    if page not in (2048, 4096, 8192, 16384, 65536):
        sys.exit('page_size 异常: %d' % page)

    newk = open(args.kernel, 'rb').read()
    if newk[:2] != b'\x1f\x8b':
        sys.exit('输入内核不是 gzip：%r（需要 Image.gz 而不是 Image）' % newk[:2])

    fields[1] = len(newk)
    hdr = struct.pack(HDR_FMT, *fields)

    body = pad_to(hdr, page) + pad_to(newk, page)
    if len(body) > args.partition_size:
        sys.exit('新镜像 %d 字节，超出分区 %d 字节' % (len(body), args.partition_size))
    img = body + b'\0' * (args.partition_size - len(body))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or '.', exist_ok=True)
    with open(args.output, 'wb') as f:
        f.write(img)

    print('boot header   : v%d, page_size=%d, kernel_addr=0x%x, tags_addr=0x%x'
          % (hdrver, page, kaddr, tags))
    print('os_version    : 0x%08x' % osver)
    print('cmdline       : %s...' % cmdline.rstrip(b'\0').decode('utf-8', 'replace')[:64])
    print('kernel_size   : %d -> %d 字节' % (ksize, len(newk)))
    print('输出          : %s (%.1f MiB)' % (args.output, len(img) / 1048576))

    # 回读校验：header / cmdline / gzip 段
    data = open(args.output, 'rb').read()
    back = struct.unpack(HDR_FMT, data[:HDR_LEN])
    assert back[0] == MAGIC, 'magic 回读失败'
    assert back[1] == len(newk), 'kernel_size 回读失败'
    assert back[12] == cmdline, 'cmdline 回读失败'
    assert data[page:page + 2] == b'\x1f\x8b', 'kernel 段偏移错误'
    assert len(data) == args.partition_size, '分区大小不符'
    print('sha256        : %s' % sha256(args.output))
    print('校验          : header / cmdline / kernel 段一致 OK')


if __name__ == '__main__':
    main()
