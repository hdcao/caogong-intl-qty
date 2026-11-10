# IFC 模型结构校验与"打开不显示"排查（v4.0.0）

> 起因：实测两个 IFC 文件在查看器里"打开一片空白 / 什么都不显示"。
> 结论：**不是查看器的问题，是文件本身缺结构关系**。本篇是排查手册 + 校验规则说明。

工具：`scripts/ifc_validate.py`

```bash
python scripts/ifc_validate.py check --file path/to/model.ifc
python scripts/ifc_validate.py selftest
```

---

## 1. "打开不显示"的五种病因（按出现频率）

### 病因 A：没有空间包含关系（最常见）
文件里有几百个 `IfcBuildingElementProxy`（带几何），但**一个
`IfcRelContainedInSpatialStructure` 都没有**。

查看器靠空间树遍历来渲染：Project → Site → Building → Storey → 元素。
元素不在任何空间结构里 = 遍历时根本走不到 = **整片不显示**。

```ifc
# 缺这条（或参数位错位）：
#32=IFCRELCONTAINEDINSPATIALSTRUCTURE('guid',#5,$,$,$,(#30,#31),#16);
#                                                            ↑RelatingStructure 必须是 Storey/Space
```

### 病因 B：聚合关系参数位错位
`IfcRelAggregates` 的 IFC4 参数位是：

```
IfcRelAggregates(GlobalId, OwnerHistory, Name, Description, RelatingObject, RelatedObjects)
                                                        ①            ②
```

写成 `(#5,$,$,$,#13,(#14))` 之外的形式（例如把 `RelatingObject` 写成 `$`、
把 `RelatedObjects` 写成散参），子对象就被丢弃，父节点成了空壳。

**自检**：用 ifcopenshell 读，看 `RelatingObject` / `RelatedElements` 是不是 `None` 或空。

### 病因 C：ObjectPlacement 类型不对
`IfcProduct.ObjectPlacement` 必须是 `IfcLocalPlacement` 或 `IfcGridPlacement`。
直接给 `IfcAxis2Placement3D` 的话，查看器拿不到相对变换，元素会堆到错误位置或被跳过。

### 病因 D：Representation 上下文不匹配
`IfcShapeRepresentation.ContextOfItems` 必须指向 `IfcGeometricRepresentationContext`
（子类型 `IfcGeometricRepresentationSubContext` 也行）。指向 `IfcGeometricRepresentationContext`
之外的上下文 → 查看器无法生成几何。

### 病因 E：挤出体参数残缺
`IfcExtrudedAreaSolid` 的 `ExtrudedDirection` 必填；`Depth` 必须是实数字面量（`0.5` 而不是 `0` 或 `$`）。
方向或深度缺失 → 几何创建失败 → 该元素空白。

---

## 2. 校验规则清单

| 规则 | 含义 | 级别 |
|---|---|---|
| IFC-01 | 文件容器完整（ISO 头 / FILE_SCHEMA / ENDSEC / END-ISO） | FAIL |
| IFC-02 | ObjectPlacement 必须是 Local/Grid Placement | FAIL |
| IFC-03 | 空间结构闭合：带几何产品全部挂进 Project 空间树 | FAIL |
| IFC-04 | RepresentationContexts 与 ShapeRepresentation 上下文一致 | FAIL |
| IFC-05 | 产品 Representation 必须是 IfcProductDefinitionShape | FAIL |
| IFC-06 | 挤出的 ExtrudedDirection 必填、Depth 为实数字面量 | FAIL |
| IFC-07 | 长度单位与坐标/挤出深度量纲合理性（启发式） | WARN |
| IFC-08 | GUID 不重复；非 ASCII 文本按 STEP `\X2\` 转义 | FAIL |
| IFC-09 | 文件膨胀预警（轮廓未共用导致几何重复） | WARN |
| IFC-10 | GlobalId 必须是合法 IFC GUID（22 字符 IFC 编码）；裸 UUID hex 被 web-ifc 拒→闪退 | FAIL |
| IFC-11 | SubContext/实体字段数按 IFC4 schema；多余括号/字段错位→web-ifc 句法错闪退 | FAIL |
| IFC-12 | 拉伸体截面必须置于竖直平面（Y=世界竖直）；零竖直厚度水平板→退化/闪退 | FAIL |
| IFC-13 | 模型坐标必须锚定**实测基准**（点云质心 / 项目 DEM），禁止手写像素+任意角；朝向按类型（岸线型 vs 丁坝型）区分 | FAIL |
| IFC-14 | `IfcExtrudedAreaSolid.ExtrudedDirection` 是**局部坐标**；沿世界水平轴挤出时须填 `(0,0,1)`，填世界向量会让结构竖直窜天 | FAIL |
| IFC-15 | **分段结构必须按里程首尾相接**：各段用同一结构中心放置会互相嵌套，模型长度退化为 `max(L_i)`，而体积仍为 `Σ(A_i·L_i)` —— 结构校验查不出 | FAIL |
| IFC-16 | 示意/插值布置须**量化自检**：OBB 重叠率 + 「可用净距 vs 所需总长」；结论区分算量缺陷与布置缺陷 | WARN |

**退出码**：0 = 通过；1 = 有 FAIL；2 = 用法/环境错。

---

## 7. 模型坐标必须锚定实测基准（"线条到处乱窜"的根因）

> 起因：某沿海项目模型在查看器里"三维空间到处乱窜的线条"。
> 结论：**不是几何错，是放置错**——结构被手填像素位置 + 任意旋转角摆放，且像素→米比例与实测差 3 倍，结构散布在数公里、近零高程、朝向各异。

### 7.1 正确做法
1. **先用实测基准定位**：优先取测区点云/DEM 的**质心 = 真实项目坐标**、**PCA 主轴角 = 真实岸线走向**（SB-01 等轴线角用 `sb_axis.json` 真值）。
2. **朝向按结构类型区分**：
   - 岸线型（海滩/护岸/潜堤/岩埂）→ 沿 PCA 角（沿岸线）；
   - 丁坝 → 垂直岸线（PCA 角 + 90°）。**不能全结构用同一角。**
3. **同区多结构**：同类分段首尾相接沿岸线铺开；异类（海滩+岩埂）平行错开；丁坝置于岸线端头。
4. **禁止**：手写 `PLACE` 像素、`to_m(px,py)` 任意比例、全局相似变换注册（锚点相对几何与真实不一致时残差可达数百米）。

### 7.2 可复用基准（`takeoff/out/geo/`）
- 各分区点云同处一个项目坐标基准（X≈0–1600 m、Y≈0–2300 m、Z≈−24~+60 m）。
- `grid_000006.json` 是项目坐标**规则 DEM**（cx/cy 50×64 节点、sp≈28.32 m/node、Z 64×50 高程网格）→ 任意 000006 像素可映射到项目坐标，但**手填像素本身不可靠**，须以质心/点云为准。

### 7.3 验收
- 回读 IFC 算各结构质心 + 整体 BBOX：结构应聚成连贯海岸带（X/Y 跨度与基准一致），而非散布数公里；**Z 跨度应仅为结构高度量级（几米~几十米），若 Z 跨度达数百米即挤出方向错**。
- web-ifc 实跑 `OpenModel→StreamAllMeshes→GetGeometry`，零失败才算通过。
- **必须渲染目视**：解析通过 ≠ 形状正确。无 matplotlib/PIL 时可用 `ifcopenshell.geom` 取世界三角形 + numpy z-buffer + zlib 自写 PNG（见 `render_model.py`）。

### 7.4 ExtrudedDirection 局部坐标陷阱（IFC-14）
`IfcExtrudedAreaSolid.ExtrudedDirection` 定义在 **Position 的局部坐标系**。若 `Axis`（局部 Z）已是世界水平轴 `(dx,dy,0)`，而 `ExtrudedDirection` 又填 `(dx,dy,0)`，它会被解析成 `dx·localX + dy·localY`，dy≈0.95~0.99 时几乎全竖直 → 结构竖着拉伸 L 米窜天。
**要沿世界水平轴挤出，`ExtrudedDirection` 必须填 `(0,0,1)`（局部 Z）**，`Depth=L`。

### 7.5 分段结构必须按里程首尾相接（IFC-15）

**症状**：结构校验全过（FAIL 0 / WARN 0），web-ifc 也能解析，但模型明显偏短。
实测：潜堤 SB-01 算量长度 2054.3 m，模型里只有 1076 m——正好等于**最长那一段**的长度。

**根因**：分段结构（按床面标高分组、或按 Section 1/2/3/4 分段）在建模时，
每一段都用**结构中心**作放置原点：
```python
sx, sy = x0 - dx*L/2, y0 - dy*L/2     # L 是本段长度
```
各段长度不同 → 起点不同 → 但**终点都收敛到同一个中心** → 长段把短段整段包住，
几何范围 = max(L_i)，而体积 = Σ(A_i × L_i)。**体积对、模型错**，且任何结构校验都查不出来。

**正确做法**：先算总长 `TL = Σ L_i`，再按里程累加偏移：
```python
def seg_center(off, L):
    return (x0 - dx*TL/2 + dx*(off + L/2),
            y0 - dy*TL/2 + dy*(off + L/2))
off = 0.0
for sg in sorted(segs, key=lambda g: g.get("s0", 0.0)):
    cx, cy = seg_center(off, sg["L"])      # ← 用 cx,cy 而不是 x0,y0
    ...
    off += sg["L"]
```

**验收（必做，成本极低）**：回读 IFC，逐结构算
`Σ|Depth|`（各段长度之和）与 `几何范围沿轴线投影长度`，两者必须相等；
再与算量长度对账。**结构校验通过 ≠ 几何正确**——`ifc_validate` 只查签名/关系/挤出，
查不出"各段叠在一起"这种语义错误。

### 7.6 布置自检要量化，不要靠"看起来对"（IFC-16）

示意布置（尤其靠插值补出来的区域）必然与相邻结构冲突。要用数字说话：
1. **OBB 采样算重叠率**：把每个结构离散成定向矩形采样点，统计"A 的点落在 B 内"的比例。
   实测本案 B-04 有 **45.5%** 落在 C-B 内——肉眼在等轴测图上看不出来，数字一算就清楚。
2. **可用长度 vs 所需长度**：沿同一方向投影，算相邻结构间的可用净距，与要放进去的总长比较。
   本案沿岸结构总长 3928 m，而 C+D+E 需 2484 m、可用岸线仅 1041 m，**缺口 1443 m**
   → 数学上就不可能不重叠，说明真实岸线在该段必有弯折，插值布置本身不成立。
3. 结论写进交付物时区分**"算量缺陷"与"布置缺陷"**：重叠不影响"断面×各自长度"的体积，
   但模型不可用于平面冲突检查、岸线占用核查、放样。

---

## 3. 生成 IFC 的正确骨架

```ifc
ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('model.ifc','2026-10-26T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('g1',$,'Project','desc',$,$,$,$,(#10));   ← RepresentationContexts = (#10)
#10=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#11,#12);
#11=IFCDIRECTION((0.,0.,1.));
#12=IFCDIRECTION((1.,0.,0.));
#20=IFCSITE('g2',$,'Site',$,$,#21,$,$,.ELEMENT.,$,$,$,$,$);
#21=IFCLOCALPLACEMENT($,#22);
#22=IFCAXIS2PLACEMENT3D(#11,$,$);
#30=IFCBUILDING('g3',$,'Building',$,$,#31,$,$,.ELEMENT.,$,$,$);
#31=IFCLOCALPLACEMENT(#21,#32);
#40=IFCBUILDINGSTOREY('g4',$,'L01',$,$,#41,$,$,.ELEMENT.,0.);
#41=IFCLOCALPLACEMENT(#31,#42);
#50=IFCBUILDINGELEMENTPROXY('g5',$,'Wall','desc',$,#41,#43,$,$);  ← ObjectPlacement=#41(Storey)
#43=IFCPRODUCTDEFINITIONSHAPE($,$,(#44));
#44=IFCSHAPEREPRESENTATION(#10,'Body','SweptSolid',(#45));
#45=IFCEXTRUDEDAREASOLID(#46,#47,#11,1.);
#46=IFCRECTANGLEPROFILEDEF(.AREA.,'p',#42,2.,3.);
#47=IFCAXIS2PLACEMENT3D(#11,$,$);
#60=IFCRELCONTAINEDINSPATIALSTRUCTURE('g6',$,$,$,$,(#50),#40);  ← RelatingStructure=#40(Storey)
#61=IFCRELAGGREGATES('g7',$,$,$,#30,(#40));                    ← Building 聚合 Storey
#62=IFCRELAGGREGATES('g8',$,$,$,#20,(#30));                    ← Site 聚合 Building
ENDSEC;
END-ISO-10303-21;
```

**五个必查点**：
1. `IFCPROJECT.RepresentationContexts` 非空且指向几何上下文；
2. 每个产品 `ObjectPlacement` 是 `IFCLOCALPLACEMENT`；
3. 每个产品 `Representation` 是 `IFCPRODUCTDEFINITIONSHAPE`；
4. 元素↔空间的 `IFCRELCONTAINEDINSPATIALSTRUCTURE` 存在且 `RelatingStructure` 指向 Storey/Space；
5. `Project → Site → Building → Storey` 的聚合链完整。

---

## 4. 校验器的实现要点

- **不依赖 ifcopenshell**：纯文本 STEP 解析，按 schema 参数位检查。
  好处是能查出 ifcopenshell 会"宽容接受"的结构错位（比如关系行的参数位写错）。
  需要 3D 几何复核时再用 ifcopenshell 单独跑。
- **空间树按关系实体反查**，不靠 `Decomposes` 反向属性（该属性在错位文件里是 `None`）。
- 校验器本身有 selftest：注入缺空间关系、删容器头、破坏 GUID 等样本，
  确认每条规则都能被触发（避免"规则写了但从没生效"）。

---

## 5. 从 IFC 取量时的额外提醒

1. **IFC 里的数量是"模型里放了多少"，不是"设计应有多少"**。
   模型可能只建了典型层、或漏建了部分构件 → 必须与二维图纸交叉核对
   （见 `model-2d-crosscheck.md`）。
2. `IfcBuildingElementProxy` 只能靠 `PredefinedType` / `Name` 区分构件类型，
   分类器必须显式登记识别规则，不要按名字猜。
3. IFC 里的 `IfcExtrudedAreaSolid` 长度单位取决于项目单位设置，
   取量前先确认 `IfcUnitAssignment`（米 / 毫米），否则面积会差 10^6。

---

## 6. 手写 STEP 导出 vs 用 API 生成（v4.5.3 新增 · 本工程实测）

### 6.1 背景
某 Ras El Hekma 项目用**手写 STEP 字符串**拼 IFC，在 BIM See（web-ifc）打开**直接闪退**，
而 ifcopenshell 能打开、回读 QTO 正常——典型的"宽松解析器能容、严格解析器崩溃"。

### 6.2 三个必崩陷阱（只有手写才会踩）
1. **GlobalId 用裸 UUID hex**：`uuid.uuid4().hex[:22]` 不是合法 IFC GUID
   （IFC GUID 是 128-bit 的特定 base64 编码）。web-ifc 严格校验 GUID → 直接闪退。
   **必须用 `ifcopenshell.guid.new()`**。
2. **SubContext / 实体字段数错、多余括号**：`IFCGEOMETRICREPRESENTATIONSUBCONTEXT`
   写成 7 字段且多一个 `)`、或把 `.MODEL_VIEW.` 放错位置 → STEP 句法错 → 闪退。
   手写极易数错字段、漏/多括号。
3. **拉伸体截面压成零厚度水平板**：`Axis2Placement3D` 把 Z 设成世界竖直、把剖面深度坐标
   落到水平轴 → 实体被压扁成水平薄片 → 严格查看器崩溃或空白。
   **正确**：placement **Z 轴 = 挤出方向（沿结构轴线，水平）**，**Y 轴 = 世界竖直**；
   剖面点 `(u=宽, v=深)` 映射 `u·X + v·Y`。

### 6.3 结论性建议（硬规则）
- **不要用手写 STEP 字符串生成交付 IFC**。一律用 `ifcopenshell` 的 `create_entity` / `createIfcXxx`
  API 生成：实体签名、字段顺序、GUID、`$`/枚举字面量全部由库保证合法。
- 注意本机 ifcopenshell 某些构建版（0.9.0post1+）**枚举值传参不带点**
  （写 `"MODEL_VIEW"` 而非 `".MODEL_VIEW."`）。
- 交付前用 **web-ifc（BIM See 同款引擎）** 实跑 `OpenModel → StreamAllMeshes → GetGeometry`
  做"严格解析器"验收，而非只看 ifcopenshell 能打开——
  ifcopenshell 容忍的缺陷 web-ifc 会崩（本项目正是因此闪退）。

### 6.4 本案验收结果（参照）
重写 `build_ifc.py` 走 API 后，web-ifc 实测：`OpenModel` 成功、`StreamAllMeshes` 36 个、
`GetGeometry` 全部成功 → 2864 顶点 / 520 三角形，**零失败**。同一文件此前手写版在 BIM See 闪退。
