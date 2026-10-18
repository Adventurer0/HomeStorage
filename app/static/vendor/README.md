# 离线扫码依赖

- 上游：[@zxing/library](https://github.com/zxing-js/library)，版本 `0.21.3`。
- 官方包：`https://registry.npmjs.org/@zxing/library/-/library-0.21.3.tgz`。
- npm 包 SHA-512 SRI：`sha512-hZHqFe2JyH/ZxviJZosZjV+2s6EDSY0O24R+FQmlWZBZXP9IqMo7S3nb3+2LBWxodJQkSurdQGnqE7KXqrYgow==`。
- 2026-10-17 从 npm 官方注册表获取固定版本，下载后核对以上完整性值，提取包内 `umd/index.min.js`，未改动解码器内容。
- 许可证原文见 `zxing-library-0.21.3.LICENSE`（上游包附带 Apache License 2.0；保留原文件）。

库由 `scanner.js` 在需要时从本站加载。运行时不访问 npm、CDN 或其他外网。
原生 `BarcodeDetector` 可用时优先使用；其他支持 HTTPS 相机的浏览器使用本地 ZXing 解码。
系统相机权限、HTTPS 和浏览器支持仍由设备决定；扫码枪与手动输入不要求相机。
