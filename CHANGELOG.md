# Changelog

## [0.2.0](https://github.com/FelipeFuhr/ffreis-website-deployer/compare/v0.1.0...v0.2.0) (2026-09-06)


### Features

* **deploy:** add content_source routing for mock content builds ([#73](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/73)) ([9581ac6](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/9581ac6e275be22fbcd3491137266f572d4299f6))
* **deploy:** add js_shared_inline_threshold and compiler embedding flag support ([9176ad6](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/9176ad67337d7391770da6fbffae2309dd125d3c))
* **deploy:** add projects and courses repo support ([#32](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/32)) ([83aebc3](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/83aebc3305611c68aa82745fc2c79314f51f4f99))
* **deployer:** emit content bundle for petlook mobile ([eee6c16](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/eee6c16c8170e1ce121d1c382dba27f4ffd63f3d))
* **deployer:** inject shared JS from ffreis-web-forms before compile ([#71](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/71)) ([72cec45](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/72cec45caaf71ff7752753a0912d74dc3102e0b9))
* **deploy:** support compiler-native single-repo sites via site_data ([#86](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/86)) ([0d16aa8](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/0d16aa86a93e7e189ad3689ed17976c2e718741a))
* **deploy:** wire inventory disable_sections → -disable-sections (Actions + local) ([#76](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/76)) ([acf3602](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/acf360230821ada97aaf6b501693b241535cd8c3))
* **deploy:** wire tracker: inventory block to -tracker-* compiler flags ([#72](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/72)) ([93d9172](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/93d91722f94029c0ab86ee215b3e0a845c354322))
* enhance smoke test — per-path checks, no hard dev skip ([#37](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/37)) ([1932b3a](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/1932b3a1a7aaab6f19bf675768d2f4e7dee321e2))
* first changes ([122207a](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/122207a221f591a10d27392941d03327c34edff5))
* first changes ([f36b90f](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/f36b90f14c8ff5d58465ef0684d3fd2a8aca8bea))
* first commit ([6566614](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/6566614b908710aff2aee9aaa25a70d72d820dbf))
* **local:** sanctioned local deploy path (deploy-local.sh) with seed-leak + bad-key guards ([#74](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/74)) ([cbe38f9](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/cbe38f97c6b97c69d035a2f8d7241388143845bb))
* platform leveling improvements ([#19](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/19)) ([132ce57](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/132ce579e2d4718dee418f834cac67b38d3b0f70))
* platform standards ([#17](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/17)) ([845ddfa](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/845ddfa7b0685464e16ed8bf35c6b6e39e5dcea0))
* setup ([ab6870f](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/ab6870fe086cb11178f3b86f771d024292c580b5))
* support optional posts repo in build pipeline ([#28](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/28)) ([a08f0f6](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/a08f0f6ae7be0bc639773316098ff45c88348792))
* weekly dev→prod drift alert ([#38](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/38)) ([c872a49](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/c872a496bce6cd80d3622b31e42fcbd6fb7cebf6))


### Bug Fixes

* ci ([8f7c941](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/8f7c9411bfd3d230124654d7e9fd7fa22d909312))
* ci ([705dca6](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/705dca6addcedeb933e28a672f128fd223bdaa44))
* ci ([9cba7b6](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/9cba7b6d01b5ddfd298a32021cb0da8e1b5f509b))
* ci ([df288d7](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/df288d7620c8bd49bf7fea914397dd4a014c47c7))
* ci ([fec28ee](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/fec28eedc2d63df5ac0bf4fe6857dd8b217c05e4))
* ci ([ab20923](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/ab2092361d681c32fa9b594eb92452351d0f7663))
* ci ([2ee7e80](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/2ee7e80cf0f8e22514b093a7bbe1a22bd5c0b76f))
* **ci:** add environment: prod to config and validate jobs ([a9c7b83](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/a9c7b836a18e5828a8e776a18c929532ed22d699))
* **ci:** bootstrap release and lint prerequisites ([#82](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/82)) ([3faee57](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/3faee57956b70b4408394625f02c81e53e438c02))
* **ci:** grant issues:write to release-please job ([#85](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/85)) ([b009189](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/b009189e1d3f1b1aebd75a6c78eae5c7a7749677))
* **ci:** quote POSTS_ARGS array to fix SC2086 shellcheck warning ([#30](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/30)) ([2ecff79](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/2ecff79ab4bdbaf3b7168448b814204754293551))
* **ci:** remove redundant standalone scorecards.yml ([#83](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/83)) ([eacc3c4](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/eacc3c453bc95072b3a5ef10a36ed7b147b085ed))
* **deploy:** select FLEET_READ_TOKEN by source repo owner ([#81](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/81)) ([7fbc7b0](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/7fbc7b037cdca5542eb7e8d014244e8af5753745))
* **deploy:** support publish.skip_smoke for IP-restricted prod sites ([#43](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/43)) ([2dabf03](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/2dabf03ea5a6a9d9defc90ee6852f2cd6cde8c18))
* **grype:** bump workflows-general SHA to prevent self-scan CVEs ([#56](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/56)) ([f044375](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/f044375559d5dd26be3bfde63ec6decc3d9fe9e0))
* labeler ([#9](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/9)) ([33f66e5](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/33f66e57f54b71af722e6f335200cea40f4526a6))
* make -clean-urls a per-site opt-out instead of unconditional ([#88](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/88)) ([53be018](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/53be018c0729999632ccfdc569b74432855917de))
* make data source optional in deploy workflow ([70c3ee4](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/70c3ee4682c037dfec619fcd68d38941ee6c0ab7))
* mount ~/.aws into publisher and invalidator containers ([#29](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/29)) ([58be75e](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/58be75e46764935beaf654f7f61c1f2f536071a0))
* pass CloudFront aliases to Python via env var, not shell expansion ([#40](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/40)) ([ccf11b5](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/ccf11b586e9682ba503418c8cc1404676aa1c193))
* **promote:** exclude sibling deployment prefixes from --delete ([#21](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/21)) ([f5a5c38](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/f5a5c38e9697029f5c5d31c87758b621136d3688))
* **promote:** skip --delete when sibling deployments share the bucket ([#23](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/23)) ([4c05aa5](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/4c05aa581dc22185ecf6f5736e7fad0d29fa10b5))
* queue deploys; create issue on watch dispatch failure ([#36](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/36)) ([a890e70](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/a890e70765a9506931a06bc4a890e8edd298e7ee))
* resolve SonarQube issues ([#59](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/59)) ([e2a7a5e](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/e2a7a5e9705c20138000c5d53c6e0b8ef9fef3c5))
* restore dev smoke test skip — WAF-403 mapped to 404 by CloudFront ([#41](https://github.com/FelipeFuhr/ffreis-website-deployer/issues/41)) ([2e351d8](https://github.com/FelipeFuhr/ffreis-website-deployer/commit/2e351d83097ba02b337f8d080f65c2d7f61bc3fb))
