# 现场记录 current：第五章本底与公式审计

- 审计时间：2026-07-17 17:49
- 范围：`media/file_library/templates/**/site/**/current/*.json`（排除 `_*.json`）
- 活动模板：35；归档/未分配：1；合计：37
- 本底规则：**第五章防护表末页表体最后两行** → 10 个本底读数 + 1 个本底报出值（不按 label/id）
- **只读审计，未修改任何模板文件**

## 1. 总览

| 桶 | 数量 | 说明 |
|----|------|------|
| pass | 1 | 本底 10+报出、绑定未混入本底、无 avg 误写、单双组一致 |
| warn | 1 | 结构基本可用，但缺章节报出规则 / 均值未全落盘 / 根级双写等 |
| fail | 27 | 本底非标准、绑定混入本底、本底误写 avg、或单双组冲突 |
| n/a | 6 | 无放射防护章节且推不出本底 |
| skip | 1 | 无 pdf.fields |

## 2. 本底（末两行）统计

| 检查项 | 活动模板数 |
|--------|-----------|
| 本底几何标准（10 读数 + 报出 + 2 行键） | 24 |
| 能推出但非标准（如读数≠10） | 5 |
| 有 RP/应有本底但推不出 | 0 |
| stored/rebuilt fieldBindings 混入本底格 | 22 |
| 本底格误写 `avg(...)` | 21 |
| 本底报出值已有 min~ 公式 | 24 |
| 仍存在根级+formSchema 双写 | 28 |

### 2.1 本底非标准 / 未识别

| 模板 | 读数N | 报出 | 行键 | 备注 |
|------|------|------|------|------|
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/16f9349228344a609593b3849b2477fc.json` | 8 | `f841` | 2 | 本底几何异常(读数8,报出=Y,行=2);根级双写;均值落盘92/95 |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/b7a262653f364c16a8eb086d7c7465a5.json` | 8 | `f841` | 2 | 本底几何异常(读数8,报出=Y,行=2);根级双写;均值落盘92/95 |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/d034390a7b884fafaac5eb99a50de397.json` | 8 | `f841` | 2 | 本底几何异常(读数8,报出=Y,行=2);根级双写;均值落盘88/95 |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/86fc79b206394658a2721607ee529bd7.json` | 8 | `f834` | 2 | 本底几何异常(读数8,报出=Y,行=2);根级双写;均值落盘89/95 |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/eafd2614d49c4f94b605b9cb1d8bedd5.json` | 8 | `f834` | 2 | 本底几何异常(读数8,报出=Y,行=2);根级双写;均值落盘89/95 |

### 2.2 绑定混入本底 / avg 误写

| 模板 | 绑定混入 | avg误写 | 样例 |
|------|----------|---------|------|
| `001-验收检测/04-C形臂/site/jxfs-js001-v30-x-202641/current/70cd495b12b64786b60228c1dc606efb.json` | 10+0 | 4 | reading_1=f570; reading_3=f572; avg@f574; avg@f575 |
| `001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641/current/0ab0bf5b8b7b48b8b6f1f07070617296.json` | 10+0 | 2 | reading_1=f1206; reading_3=f1208; avg@f1210; avg@f1211 |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/2fdb9b34455241b998f0d743b09c03fb.json` | 10+0 | 2 | reading_1=f1206; reading_3=f1208; avg@f1210; avg@f1211 |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/dfbbf059a6344f46838c862e5d2a7a84.json` | 10+0 | 2 | reading_1=f1206; reading_2=f1208; avg@f1212; avg@f1213 |
| `001-验收检测/07-乳腺DR/site/xdr-1/current/f421b3a50106469b8cdc8cfcd4294062.json` | 10+0 | 4 | reading_1=f664; reading_3=f665; avg@f666; avg@f668 |
| `001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/165e9bbb5fad4e6abbdcbe994fe8efbd.json` | 10+0 | 2 | reading_1=f878; reading_3=f920; avg@f879; avg@f883 |
| `001-验收检测/08-口腔CBCT/site/xcbct-1/current/515ca0e9435046f095bd0e9be42a32ac.json` | 10+0 | 2 | reading_1=f864; reading_3=f865; avg@f866; avg@f870 |
| `001-验收检测/09-口腔全景/site/panorama-accept-site/current/a0db9042347748a1aec9891e620d14a0.json` | 10+0 | 2 | reading_1=f1016; reading_3=f1017; avg@f1018; avg@f1023 |
| `001-验收检测/10口内牙片机/site/jxfs-js008-v30-x202641/current/ba17b22073b14f41927d39a5958c6652.json` | 8+0 | 2 | reading_1=f1050; reading_3=f1052; avg@f1058; avg@f1059 |
| `002-状态检测/01-CT/site/jxfs-js009-v30-xct202641-2/current/1506156d24d24a218e67c4aa62fd7998.json` | 1+0 | 0 | reading_1=f584 |
| `002-状态检测/03DSA/site/jxfs-js001-v30-x-202641-2/current/43d965c2f0f9496595ffcac1c408aeae.json` | 7+0 | 2 | reading_1=f474; reading_1=f533; avg@f537; avg@f542 |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/06304a2018d44efeb1203bed8c129746.json` | 6+0 | 2 | reading_1=f570; reading_3=f576; avg@f578; avg@f579 |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/15051fe8d1944c7780469837234796f0.json` | 10+0 | 4 | reading_1=f570; reading_3=f572; avg@f574; avg@f575 |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/66f3fa17ed084162bdce06d3ca863324.json` | 10+0 | 2 | reading_1=f1206; reading_3=f1208; avg@f1210; avg@f1211 |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/7ae33f01799244939638dae5d9ea3de8.json` | 10+0 | 2 | reading_1=f1206; reading_2=f1208; avg@f1212; avg@f1213 |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/85a6888b0eb84b1499c06bc47d4983bc.json` | 10+0 | 2 | reading_1=f1206; reading_3=f1208; avg@f1210; avg@f1211 |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/acb971eecb074e0d95d4db351801d4c3.json` | 10+0 | 2 | reading_1=f1206; reading_2=f1208; avg@f1212; avg@f1213 |
| `002-状态检测/07-乳腺DR/site/xdr/current/a708a344436f43b8b48a81be57fac601.json` | 10+0 | 4 | reading_1=f662; reading_3=f663; avg@f664; avg@f666 |
| `002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/d699bf995a2f44b9a5627fb23d3eb4e6.json` | 10+0 | 2 | reading_1=f1017; reading_3=f1018; avg@f1019; avg@f1024 |
| `002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/54dedaecd24b4db2ab307ed4660cff25.json` | 10+0 | 2 | reading_1=f886; reading_3=f887; avg@f888; avg@f892 |
| `002-状态检测/09-口腔全景/site/panorama-status-site/current/4588dc1387ff4b5580642a24b37bf77c.json` | 10+0 | 2 | reading_1=f1018; reading_3=f1019; avg@f1020; avg@f1025 |
| `002-状态检测/10口内牙片机/site/jxfs-js008-v30-x202641-2/current/6d0964c36a71439e87dee6d271cacb02.json` | 8+0 | 2 | reading_1=f1050; reading_3=f1052; avg@f1058; avg@f1059 |

### 2.3 本底几何明细（活动模板）

| 模板 | 读数N | 报出 | 行 | 几何OK | 绑定混入 | avg误写 | 报出公式 |
|------|------|------|----|--------|----------|---------|----------|
| `001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/current/891c79d567e142e7a4b33b4335ab5ae4.json` | 10 | `f603` | 2 | Y | 0/0 | 0 | Y |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/16f9349228344a609593b3849b2477fc.json` | 8 | `f841` | 2 | N | 0/0 | 0 | Y |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/b7a262653f364c16a8eb086d7c7465a5.json` | 8 | `f841` | 2 | N | 0/0 | 0 | Y |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/d034390a7b884fafaac5eb99a50de397.json` | 8 | `f841` | 2 | N | 0/0 | 0 | Y |
| `001-验收检测/03-DSA/site/jxfs-js001-v30-x-202641-1/current/8aa1021a32bd4e039a5c790447682e40.json` | 10 | `f620` | 2 | Y | 0/0 | 0 | Y |
| `001-验收检测/04-C形臂/site/jxfs-js001-v30-x-202641/current/70cd495b12b64786b60228c1dc606efb.json` | 10 | `f494` | 2 | Y | 10/0 | 4 | Y |
| `001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641/current/0ab0bf5b8b7b48b8b6f1f07070617296.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | Y |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/2fdb9b34455241b998f0d743b09c03fb.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | Y |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/dfbbf059a6344f46838c862e5d2a7a84.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | N |
| `001-验收检测/07-乳腺DR/site/xdr-1/current/f421b3a50106469b8cdc8cfcd4294062.json` | 10 | `f574` | 2 | Y | 10/0 | 4 | Y |
| `001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/165e9bbb5fad4e6abbdcbe994fe8efbd.json` | 10 | `f846` | 2 | Y | 10/0 | 2 | Y |
| `001-验收检测/08-口腔CBCT/site/xcbct-1/current/515ca0e9435046f095bd0e9be42a32ac.json` | 10 | `f833` | 2 | Y | 10/0 | 2 | Y |
| `001-验收检测/09-口腔全景/site/panorama-accept-site/current/a0db9042347748a1aec9891e620d14a0.json` | 10 | `f874` | 2 | Y | 10/0 | 2 | Y |
| `001-验收检测/10口内牙片机/site/jxfs-js008-v30-x202641/current/ba17b22073b14f41927d39a5958c6652.json` | 10 | `f887` | 2 | Y | 8/0 | 2 | Y |
| `002-状态检测/01-CT/site/jxfs-js009-v30-xct202641-2/current/1506156d24d24a218e67c4aa62fd7998.json` | 10 | `f584` | 2 | Y | 1/0 | 0 | Y |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/86fc79b206394658a2721607ee529bd7.json` | 8 | `f834` | 2 | N | 0/0 | 0 | Y |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/eafd2614d49c4f94b605b9cb1d8bedd5.json` | 8 | `f834` | 2 | N | 0/0 | 0 | Y |
| `002-状态检测/03DSA/site/jxfs-js001-v30-x-202641-2/current/43d965c2f0f9496595ffcac1c408aeae.json` | 10 | `f474` | 2 | Y | 7/0 | 2 | N |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/06304a2018d44efeb1203bed8c129746.json` | 10 | `f494` | 2 | Y | 6/0 | 2 | N |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/15051fe8d1944c7780469837234796f0.json` | 10 | `f494` | 2 | Y | 10/0 | 4 | Y |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/66f3fa17ed084162bdce06d3ca863324.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | Y |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/7ae33f01799244939638dae5d9ea3de8.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | N |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/85a6888b0eb84b1499c06bc47d4983bc.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | Y |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/acb971eecb074e0d95d4db351801d4c3.json` | 10 | `f1010` | 2 | Y | 10/0 | 2 | N |
| `002-状态检测/07-乳腺DR/site/xdr/current/a708a344436f43b8b48a81be57fac601.json` | 10 | `f573` | 2 | Y | 10/0 | 4 | Y |
| `002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/d699bf995a2f44b9a5627fb23d3eb4e6.json` | 10 | `f876` | 2 | Y | 10/0 | 2 | Y |
| `002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/54dedaecd24b4db2ab307ed4660cff25.json` | 10 | `f854` | 2 | Y | 10/0 | 2 | Y |
| `002-状态检测/09-口腔全景/site/panorama-status-site/current/4588dc1387ff4b5580642a24b37bf77c.json` | 10 | `f876` | 2 | Y | 10/0 | 2 | Y |
| `002-状态检测/10口内牙片机/site/jxfs-js008-v30-x202641-2/current/6d0964c36a71439e87dee6d271cacb02.json` | 10 | `f887` | 2 | Y | 8/0 | 2 | Y |

## 3. 章节公式与单组/双组

| 检查项 | 数量 |
|--------|------|
| 版式单组 | 9 |
| 版式双组 | 20 |
| 章节 meanFormula 与版式不一致 | 0 |
| 有 reportValueRules | 18 |
| 有 annualDoseRules | 9 |

### 3.1 单组/双组与均值落盘

| 模板 | 版式 | mode | groups | 均值落盘 | 报出规则 | 年剂量规则 |
|------|------|------|--------|----------|----------|------------|
| `001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/current/891c79d567e142e7a4b33b4335ab5ae4.json` | single | `per_row_avg` | 1 | 62/63 | 1 | 0 |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/16f9349228344a609593b3849b2477fc.json` | dual | `per_row_avg_dual` | 2 | 92/95 | 2 | 2 |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/b7a262653f364c16a8eb086d7c7465a5.json` | dual | `per_row_avg_dual` | 2 | 92/95 | 2 | 2 |
| `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/d034390a7b884fafaac5eb99a50de397.json` | dual | `per_row_avg_dual` | 2 | 88/95 | 2 | 2 |
| `001-验收检测/03-DSA/site/jxfs-js001-v30-x-202641-1/current/8aa1021a32bd4e039a5c790447682e40.json` | single | `per_row_avg` | 1 | 65/65 | 1 | 0 |
| `001-验收检测/04-C形臂/site/jxfs-js001-v30-x-202641/current/70cd495b12b64786b60228c1dc606efb.json` | single | `per_row_avg` | 1 | 61/65 | 0 | 0 |
| `001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641/current/0ab0bf5b8b7b48b8b6f1f07070617296.json` | dual | `per_row_avg_dual` | 2 | 106/128 | 2 | 2 |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/2fdb9b34455241b998f0d743b09c03fb.json` | dual | `per_row_avg_dual` | 2 | 106/128 | 2 | 2 |
| `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/dfbbf059a6344f46838c862e5d2a7a84.json` | dual | `per_row_avg_dual` | 2 | 104/128 | 0 | 0 |
| `001-验收检测/07-乳腺DR/site/xdr-1/current/f421b3a50106469b8cdc8cfcd4294062.json` | single | `per_row_avg` | 1 | 62/62 | 0 | 0 |
| `001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/165e9bbb5fad4e6abbdcbe994fe8efbd.json` | dual | `per_row_avg_dual` | 2 | 113/132 | 2 | 0 |
| `001-验收检测/08-口腔CBCT/site/xcbct-1/current/515ca0e9435046f095bd0e9be42a32ac.json` | dual | `per_row_avg_dual` | 2 | 113/132 | 2 | 0 |
| `001-验收检测/09-口腔全景/site/panorama-accept-site/current/a0db9042347748a1aec9891e620d14a0.json` | dual | `per_row_avg_dual` | 2 | 108/128 | 0 | 0 |
| `001-验收检测/10口内牙片机/site/jxfs-js008-v30-x202641/current/ba17b22073b14f41927d39a5958c6652.json` | dual | `per_row_avg_dual` | 2 | 120/128 | 2 | 0 |
| `002-状态检测/01-CT/site/jxfs-js009-v30-xct202641-2/current/1506156d24d24a218e67c4aa62fd7998.json` | single | `per_row_avg` | 1 | 64/64 | 1 | 0 |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/86fc79b206394658a2721607ee529bd7.json` | dual | `per_row_avg_dual` | 2 | 89/95 | 2 | 2 |
| `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/eafd2614d49c4f94b605b9cb1d8bedd5.json` | dual | `per_row_avg_dual` | 2 | 89/95 | 2 | 2 |
| `002-状态检测/03DSA/site/jxfs-js001-v30-x-202641-2/current/43d965c2f0f9496595ffcac1c408aeae.json` | single | `per_row_avg` | 1 | 65/65 | 0 | 0 |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/06304a2018d44efeb1203bed8c129746.json` | single | `per_row_avg` | 1 | 61/63 | 0 | 0 |
| `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/15051fe8d1944c7780469837234796f0.json` | single | `per_row_avg` | 1 | 61/65 | 0 | 0 |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/66f3fa17ed084162bdce06d3ca863324.json` | dual | `per_row_avg_dual` | 2 | 106/128 | 2 | 2 |
| `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/7ae33f01799244939638dae5d9ea3de8.json` | dual | `per_row_avg_dual` | 2 | 104/128 | 0 | 0 |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/85a6888b0eb84b1499c06bc47d4983bc.json` | dual | `per_row_avg_dual` | 2 | 106/128 | 2 | 2 |
| `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/acb971eecb074e0d95d4db351801d4c3.json` | dual | `per_row_avg_dual` | 2 | 104/128 | 0 | 0 |
| `002-状态检测/07-乳腺DR/site/xdr/current/a708a344436f43b8b48a81be57fac601.json` | single | `per_row_avg` | 1 | 62/62 | 0 | 0 |
| `002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/d699bf995a2f44b9a5627fb23d3eb4e6.json` | dual | `per_row_avg_dual` | 2 | 108/128 | 2 | 0 |
| `002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/54dedaecd24b4db2ab307ed4660cff25.json` | dual | `per_row_avg_dual` | 2 | 111/130 | 2 | 0 |
| `002-状态检测/09-口腔全景/site/panorama-status-site/current/4588dc1387ff4b5580642a24b37bf77c.json` | dual | `per_row_avg_dual` | 2 | 108/128 | 0 | 0 |
| `002-状态检测/10口内牙片机/site/jxfs-js008-v30-x202641-2/current/6d0964c36a71439e87dee6d271cacb02.json` | dual | `per_row_avg_dual` | 2 | 120/128 | 2 | 0 |

### 3.2 章节报出值 / 年剂量规则内容

#### `001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/current/891c79d567e142e7a4b33b4335ab5ae4.json`

- 报出1: cond=`（空）` expr=`{mean} * f209` （mean=True, mean2=False）

#### `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/16f9349228344a609593b3849b2477fc.json`

- 报出1: cond=`（空）` expr=`{mean}*f927` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f270` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`f844*f845*50`
- 年剂量2: cond=`{report2}` expr=`f844*f845*50`

#### `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/b7a262653f364c16a8eb086d7c7465a5.json`

- 报出1: cond=`（空）` expr=`{mean}*f927` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f270` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`f844*f845*50`
- 年剂量2: cond=`{report2}` expr=`f844*f845*50`

#### `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/d034390a7b884fafaac5eb99a50de397.json`

- 报出1: cond=`（空）` expr=`{mean}*f927` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f270` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`f844*f845*50`
- 年剂量2: cond=`{report2}` expr=`f844*f845*50`

#### `001-验收检测/03-DSA/site/jxfs-js001-v30-x-202641-1/current/8aa1021a32bd4e039a5c790447682e40.json`

- 报出1: cond=`（空）` expr=`{mean}*f77` （mean=True, mean2=False）

#### `001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641/current/0ab0bf5b8b7b48b8b6f1f07070617296.json`

- 报出1: cond=`（空）` expr=`{mean}*f248` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f250` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`{report}*f1013*f1014*50`
- 年剂量2: cond=`{report2}>25` expr=`{report2}*f1013*f1014*50`

#### `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/2fdb9b34455241b998f0d743b09c03fb.json`

- 报出1: cond=`（空）` expr=`{mean}*f248` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f250` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`{report}*f1013*f1014*50`
- 年剂量2: cond=`{report2}>25` expr=`{report2}*f1013*f1014*50`

#### `001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/165e9bbb5fad4e6abbdcbe994fe8efbd.json`

- 报出1: cond=`（空）` expr=`{mean}*f149` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f151` （mean=False, mean2=True）

#### `001-验收检测/08-口腔CBCT/site/xcbct-1/current/515ca0e9435046f095bd0e9be42a32ac.json`

- 报出1: cond=`（空）` expr=`if({mean},{mean}*f182,"")` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`if({mean2},{mean2}*f182,"")` （mean=False, mean2=True）

#### `001-验收检测/10口内牙片机/site/jxfs-js008-v30-x202641/current/ba17b22073b14f41927d39a5958c6652.json`

- 报出1: cond=`（空）` expr=`{mean}*f166` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f168` （mean=False, mean2=True）

#### `002-状态检测/01-CT/site/jxfs-js009-v30-xct202641-2/current/1506156d24d24a218e67c4aa62fd7998.json`

- 报出1: cond=`（空）` expr=`{mean}*f205` （mean=True, mean2=False）

#### `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/86fc79b206394658a2721607ee529bd7.json`

- 报出1: cond=`（空）` expr=`{mean}*f919` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f263` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`f837*f838*50`
- 年剂量2: cond=`{report2}>25` expr=`f837*f838*50`

#### `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/eafd2614d49c4f94b605b9cb1d8bedd5.json`

- 报出1: cond=`（空）` expr=`{mean}*f919` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f263` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`f837*f838*50`
- 年剂量2: cond=`{report2}>25` expr=`f837*f838*50`

#### `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/66f3fa17ed084162bdce06d3ca863324.json`

- 报出1: cond=`（空）` expr=`{mean}*f248` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f250` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`{report}*f1013*f1014*50`
- 年剂量2: cond=`{report2}>25` expr=`{report2}*f1013*f1014*50`

#### `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/85a6888b0eb84b1499c06bc47d4983bc.json`

- 报出1: cond=`（空）` expr=`{mean}*f248` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f250` （mean=False, mean2=True）
- 年剂量1: cond=`{report}>25` expr=`{report}*f1013*f1014*50`
- 年剂量2: cond=`{report2}>25` expr=`{report2}*f1013*f1014*50`

#### `002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/d699bf995a2f44b9a5627fb23d3eb4e6.json`

- 报出1: cond=`（空）` expr=`{mean}*f984` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f158` （mean=False, mean2=True）

#### `002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/54dedaecd24b4db2ab307ed4660cff25.json`

- 报出1: cond=`（空）` expr=`{mean}*f149` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f151` （mean=False, mean2=True）

#### `002-状态检测/10口内牙片机/site/jxfs-js008-v30-x202641-2/current/6d0964c36a71439e87dee6d271cacb02.json`

- 报出1: cond=`（空）` expr=`{mean}*f166` （mean=True, mean2=False）
- 报出2: cond=`（空）` expr=`{mean2}*f168` （mean=False, mean2=True）

## 4. 分桶清单

### 4.1 FAIL（27）

- `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/16f9349228344a609593b3849b2477fc.json`
  - 本底几何异常(读数8,报出=Y,行=2)
  - 根级双写
  - 均值落盘92/95
- `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/b7a262653f364c16a8eb086d7c7465a5.json`
  - 本底几何异常(读数8,报出=Y,行=2)
  - 根级双写
  - 均值落盘92/95
- `001-验收检测/02-DR/site/jxfs-js010-v30-xdr202641/current/d034390a7b884fafaac5eb99a50de397.json`
  - 本底几何异常(读数8,报出=Y,行=2)
  - 根级双写
  - 均值落盘88/95
- `001-验收检测/04-C形臂/site/jxfs-js001-v30-x-202641/current/70cd495b12b64786b60228c1dc606efb.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×4
  - 无reportValueRules
  - 根级双写
  - 均值落盘61/65
- `001-验收检测/05-胃肠机/site/jxfs-js115-v20-x-202641/current/0ab0bf5b8b7b48b8b6f1f07070617296.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘106/128
- `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/2fdb9b34455241b998f0d743b09c03fb.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘106/128
- `001-验收检测/06-动态DR/site/jxfs-js115-v20-x-202641-1/current/dfbbf059a6344f46838c862e5d2a7a84.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘104/128
- `001-验收检测/07-乳腺DR/site/xdr-1/current/f421b3a50106469b8cdc8cfcd4294062.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×4
  - 无reportValueRules
  - 根级双写
- `001-验收检测/08-口腔CBCT/site/jxfs-js116-v20-xcbct-202641/current/165e9bbb5fad4e6abbdcbe994fe8efbd.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘113/132
- `001-验收检测/08-口腔CBCT/site/xcbct-1/current/515ca0e9435046f095bd0e9be42a32ac.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘113/132
- `001-验收检测/09-口腔全景/site/panorama-accept-site/current/a0db9042347748a1aec9891e620d14a0.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘108/128
- `001-验收检测/10口内牙片机/site/jxfs-js008-v30-x202641/current/ba17b22073b14f41927d39a5958c6652.json`
  - 本底几何OK
  - stored绑定混入本底×8
  - 本底误写avg×2
  - 根级双写
  - 均值落盘120/128
- `002-状态检测/01-CT/site/jxfs-js009-v30-xct202641-2/current/1506156d24d24a218e67c4aa62fd7998.json`
  - 本底几何OK
  - stored绑定混入本底×1
  - 根级双写
- `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/86fc79b206394658a2721607ee529bd7.json`
  - 本底几何异常(读数8,报出=Y,行=2)
  - 根级双写
  - 均值落盘89/95
- `002-状态检测/02DR/site/jxfs-js010-v30-xdr202641-2/current/eafd2614d49c4f94b605b9cb1d8bedd5.json`
  - 本底几何异常(读数8,报出=Y,行=2)
  - 根级双写
  - 均值落盘89/95
- `002-状态检测/03DSA/site/jxfs-js001-v30-x-202641-2/current/43d965c2f0f9496595ffcac1c408aeae.json`
  - 本底几何OK
  - stored绑定混入本底×7
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
- `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/06304a2018d44efeb1203bed8c129746.json`
  - 本底几何OK
  - stored绑定混入本底×6
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘61/63
- `002-状态检测/04C形臂/site/jxfs-js001-v30-x-202641-3/current/15051fe8d1944c7780469837234796f0.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×4
  - 无reportValueRules
  - 根级双写
  - 均值落盘61/65
- `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/66f3fa17ed084162bdce06d3ca863324.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘106/128
- `002-状态检测/05胃肠机/site/jxfs-js115-v20-x-202641-2/current/7ae33f01799244939638dae5d9ea3de8.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘104/128
- `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/85a6888b0eb84b1499c06bc47d4983bc.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘106/128
- `002-状态检测/06动态DR/site/jxfs-js115-v20-x-202641-3/current/acb971eecb074e0d95d4db351801d4c3.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘104/128
- `002-状态检测/07-乳腺DR/site/xdr/current/a708a344436f43b8b48a81be57fac601.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×4
  - 无reportValueRules
  - 根级双写
- `002-状态检测/08-口腔CBCT/site/jxfs-js008-v30-x202641-1/current/d699bf995a2f44b9a5627fb23d3eb4e6.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘108/128
- `002-状态检测/08-口腔CBCT/site/oral-cbct-status-site/current/54dedaecd24b4db2ab307ed4660cff25.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 根级双写
  - 均值落盘111/130
- `002-状态检测/09-口腔全景/site/panorama-status-site/current/4588dc1387ff4b5580642a24b37bf77c.json`
  - 本底几何OK
  - stored绑定混入本底×10
  - 本底误写avg×2
  - 无reportValueRules
  - 根级双写
  - 均值落盘108/128
- `002-状态检测/10口内牙片机/site/jxfs-js008-v30-x202641-2/current/6d0964c36a71439e87dee6d271cacb02.json`
  - 本底几何OK
  - stored绑定混入本底×8
  - 本底误写avg×2
  - 根级双写
  - 均值落盘120/128

### 4.2 WARN（1）

- `001-验收检测/01-CT/site/jxfs-js009-v30-xct202641-1/current/891c79d567e142e7a4b33b4335ab5ae4.json`
  - 本底几何OK
  - 根级双写
  - 均值落盘62/63

### 4.3 PASS（1）

- `001-验收检测/03-DSA/site/jxfs-js001-v30-x-202641-1/current/8aa1021a32bd4e039a5c790447682e40.json`
  - 本底几何OK

### 4.4 N/A（6）

- `001-验收检测/03-DSA/site/jxfs-js117-v20-cbctc202641/current/5e33a6d5b33f441bbafa2f376ef44afb.json`
- `001-验收检测/11-加速器中的CBCT/site/259072-01cbctct/current/a3706fa92e534df89999a87a45651114.json`
- `002-状态检测/03DSA/site/jxfs-js117-v20-cbctc202641-1/current/d9adb0a753864e75b98c1eb02c04da59.json`
- `002-状态检测/11加速器中的CBCT/site/jxfs-js118-v20-cbct/current/9f48422d841e4968ac96cce76dda44fb.json`
- `004-环保检测报告/宠物环境监测/site/jxfs-js108-v10-x20211008-1/current/eb6e442a51c6449490bd54eade379c99.json`
- `004-环保检测报告/抚州市第一本底监测/site/jxfs-js107-v10-x-20211008/current/bf7649b9aaf34217ba9f8d1f9f9a09d4.json`

## 5. 归档 / 未分配（仅列路径与桶）

| 路径 | bucket |
|------|--------|
| `_archived/20260703-口内牙片机-drct9遗留/site/jxfs-js008-v30-x202641-3/current/9c3e6be452bf427da69958e9f0d4d86c.json` | archived |

## 6. 结论摘要

1. **本底末两行几何**：活动模板中 24/29 符合「10 读数 + 报出 + 2 行」；另有 5 份能识别但读数格数不是 10（常见于 DR 等版式，末两行合计 8 格，需业务确认是否缺框）。
2. **绑定污染**：22 份的 `fieldBindings` 仍把本底格当成测量读数/均值（磁盘历史绑定未按新规则重建）。
3. **本底误写 avg**：21 份本底格上仍残留 `avg(...)`。
4. **单双组**：双组 20、单组 9；不一致 0。
5. **章节报出规则**：18 份已配置 `reportValueRules`；9 份有年剂量规则。
6. **均值公式落盘**：多数模板均值覆盖率较高，但未 100% 的不单独算结构失败（编辑器「完成」/保存才会刷全）。

