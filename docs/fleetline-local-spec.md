# Fleetline — ร่างสเปกสำหรับพัฒนาต่อบน Local

สถานะเอกสาร: ร่างเพื่อนำไปพัฒนาและ UAT ต่อ ไม่ใช่ข้อเรียกร้องให้ Orca อนุมัติสิทธ์เพิ่ม

อ้างอิงซอร์ส: https://cursor.com/codebase/theeranat-sringamdee/fleetline

Verified commit: `b7dc03726bcb2885b54c7178ef6c5ced4a9d52eb`

สภาพแวดล้อมที่ตรวจแล้ว: Orca 1.4.251, Codex CLI 0.167.0, Node >= 22

## 1. เป้าหมายและขอบเขต

Fleetline เป็น orchestration layer ที่เลือกติดตั้ง/ถอดส่วนเสริมได้แบบ plugin architecture ของ deepseek-harness นำเฉพาะแนวคิด lifecycle/modularity ไม่เอา agent loop ของมัน

- Orca เป็น UI และเจ้าของ workspace/worktree
- Codex เป็น native worker ที่ Orca เปิดใช้งาน
- Portable skills เป็นคู่มือ workflow
- Fleetline kernel จัดการ start/stop, service registry, event listeners, backend registry
- ปลั๊กอิน sidebar อ่านข้อมูลอย่างเดียว
- ไม่ scrape PTY/TUI, ไม่เรียก Claude/Codex ในลูปซ้ำ, ไม่ถือ API keys, ไม่ bypass permissions

ปัจจุบันยังไม่ใช่ IDE/terminal/agent runtime ใหม่ และยังไม่มี unified toggle dashboard แบบ dsh

## 2. สิ่งที่มีแล้วและผลทดสอบต้นทาง

1. Fleetline kernel: start/stop profile, listeners/backends ถูกถอดตาม lifecycle
2. Orca adapter: Orca 1.4.251 launch receipt, repo/worktree discovery, `doctor --live`, `discover --live`
3. Executable: `bin/fleetline.mjs` ใช้คำสั่งผ่าน `npx fleetline` ได้
4. Native Codex ผ่าน Orca: ผ่าน read-only test ที่ให้ Codex อ่าน skill จาก workspace
5. Fleetline sidebar: เปิดแผงและแสดง fleetline, terminal 2 รายการ, codex `session idle` และ `command outcome not reported`
6. สิทธิ์ sidebar: `workspace:read` และ `agent:read` เท่านั้น; หลังเปลี่ยน panel `needsReconsent: false`
7. Language plugin: นับภาษาด้วยไฟล์จริงใน process มี fixture, exclusions, binary detection, percentages 100%
8. ผลทดสอบใน cloud ของ commit นี้: `npm ci`, typecheck, lint, 37 tests; clean test ก่อนหน้าผ่าน `npx fleetline --help`, demo

## 3. Flow การทำงาน

### 3.1 เปิด Codex ใน workspace เดิม
ผู้ใช้/CLI เลือก backend Codex → Fleetline สร้าง Orca CLI argv → `--execute` เป็นช่องทางเดียวที่สั่ง Orca ให้เปิด worker → Orca เป็นเจ้าของ terminal/session → Codex อ่าน portable skill และทำงานภายใต้ข้อจำกัด Orca/Codex → sidebar รับอ่านสถานะ

คำสั่งปกติของ `plan` ต้องเป็น dry-run ห้ามเปิด worker นอกเหนือ `--execute`

### 3.2 Sidebar
Sandboxed HTML เรียก `workspace.readContext` และ `agents.list` → แสดงชื่อ workspace, branch, terminal count, session state → subscribe `agent.status.changed` เพื่อเก็บ outcome จาก event ทางการ

### 3.3 สถานะนี้ต้องตีความอย่างไร
- `done` → `session idle` ไม่ใช่สำเร็จ
- `working`, `blocked`, `waiting` → session state ตามชื่อ
- command succeeded/failed เฉพาะ official `outcome` หรือ `mainAgent.outcome` success/failure เท่านั้น
- ไม่มี official outcome → `command outcome not reported`
- ชื่อเทอร์มินอลเช่น `exit 1` ไม่ใช้เป็นหลักฐานเพื่อดูความสำเร็จ/ล้มเหลว; ไม่ scrape terminal เพื่อหา exit code

### 3.4 Language plugin
เริ่มโดยอ่าน root project ใน process ที่ได้รับอนุญาต → เดินไฟล์ตาม extension → ข้าม dependencies/generated/binary → รวมจำนวน bytes → คำนวณ percentages ทศนิยม 1 ตำแหน่งรวม 100 → stop แล้วไม่เหลือ listeners

## 4. Plugin specs

### 4.1 Kernel plugin
- มี stable ID, start/stop ที่ชัดเจน
- ไม่ import/launch backend ถ้าไม่ได้เลือกใน profile
- ใช้ services/events ของ kernel
- เมื่อ stop ต้องล้าง listener/subscription และ registry ที่ตัวเองเพิ่ม
- แต่ละสิทธิ์ต้องมีขอบเขตต่ำสุด
- plugin ต้องมีเอกสาร, automated test และ UAT

### 4.2 Fleetline status sidebar
Manifest `integrations/orca-sidebar/orca-plugin.json`
- manifestVersion 2, execution `sandboxed-ui`, pluginApi 1, Orca >= 1.4.246
- มีเพียง `workspace:read`, `agent:read`
- ห้าม `--approve` อัตโนมัติ ห้ามเพิ่มสิทธิ์โดยไม่ขออนุญาต
- ฟังก์ชันเดียวตาม data ที่ Orca ให้ ไม่เปิด/เขียนไฟล์โปรเจกต์ และไม่ส่ง terminal input
- ปิดได้จาก Orca plugin UI เท่านั้น; Orca เป็นเจ้าของ load/unload

### 4.3 Project languages
- ต้นแบบเป็น optional kernel plugin เรียก `npx fleetline languages`
- นับ TypeScript/JavaScript/CSS/HTML/Markdown และภาษาอื่นตาม mapping นามสกุล
- ข้าม `node_modules`, `dist`, assets รูปภาพและ binary
- fixture ต้นนำได้ TypeScript 40.6, HTML 24.2, JavaScript 16.4, CSS 13.3, Markdown 5.5
- รวม % 100 ด้วยวิธีหลีกเลี่ยงปัญหาการปัด
- เป็น read-only scan ตอนรัน ไม่ใช่ watcher ที่คอยทำงานอยู่

### 4.4 Portable skills (ไม่ใช่ plugin)
- Codex source: `skills/codex/orchestrate-with-orca/SKILL.md`
- Claude source: `skills/claude-code/orchestrate-with-orca/SKILL.md`
- Codex จริงอ่านจาก `.agents/skills/orchestrate-with-orca/SKILL.md` ตอน import folder workspace
- Skill บอกวิธีทำงาน ไม่เพิ่ม capability ให้ kernel/sidebar และไม่ควรมี credential

## 5. งานที่ต้องพัฒนาบน Local

### ขั้นที่ 1 — ตั้งและตรวจงานเดิม
- Clone private Fleetline แล้ว checkout commit นี้
- `npm ci` ต้องรัน `prepare` แล้วสร้าง bin ได้
- รัน test/lint/typecheck/demo
- ติดตั้ง official Orca และ Codex CLI ลงชื่อเข้าใช้ด้วยตัวเอง
- ตรวจ `doctor --live`, `discover --live` แบบอ่านอย่างเดียว
- เลือกโฟลเดอร์ที่ตัวเอง clone จริงใน Orca ไม่ใช้ folder ที่ยังไม่มี `.git`

### ขั้นที่ 2 — รักษา CLI compatibility
- ทดสอบ read launch receipt ทั้ง Orca JSON เก่าและใหม่ (`result.outcome`, `result.receipt`, `result.prompt`, legacy `result.launch`)
- ตรวจ worktree listing ปัจจุบัน (`result.worktrees`, displayName, agents)
- `sessionId` อ่านเฉพาะ `result.sessionId`, `result.launch.sessionId`, `result.launch.outcome.sessionId`
- ห้ามเดา id จากชื่องาน/pane/terminal
- รักษา executable, lockfile bin และ packaging ให้ตรงกัน

### ขั้นที่ 3 — Lifecycle/toggle ให้ใกล้ dsh
- Spec ใหม่ให้ unified loadout ภายใน Fleetline: enable/disable ชื่อ plugin, query สถานะ, start/stop ทั้งชุด
- ปลั๊กอินปิดแล้วต้องไม่ respond event หรือ leave backend ใน registry
- พิจารณาแผง Fleetline control ถ้าต้องการ UI
- ข้อจำกัด: Orca-hosted sidebar toggle ยังอยู่ใน Orca settings; plugin ตัวนี้ยังควบคุมปลั๊กอิน Orca อื่นไม่ได้
- Language sidebar ใน Orca **อยู่นอกขอบเขตงานปัจจุบัน** จนกว่า API จะมี read-only file list จำกัด focused workspace
- อย่า claim expert module/harness scheduler ว่าเกิดแล้ว

### ขั้นที่ 4 — Docs/Skills
- อัปเดต architecture, permissions, command usage และข้อจำกัด
- Skill สะท้อนวิธียิง Orca CLI ปัจจุบันและ dry-run vs execute
- Doc ไม่ควรใช้ผล fixture แทนผล live ต้องระบุแยกกัน

## 6. Acceptance criteria และ Automated Test

ทุกงานแก้เสร็จต้องรันนี้ใน clean checkout/test directory:

```sh
npm ci
npx --no-install fleetline --help
npm test
npm run lint
npm run typecheck
npm run demo
npx --no-install fleetline doctor
npx --no-install fleetline discover
npx --no-install fleetline languages
node integrations/orca-sidebar/status-contract.test.mjs
```

A1 CLI — help มีรายการคำสั่งจริง, dry-run default, unknown option ต้อง error ไม่ silent success

A2 Kernel — เริ่มเฉพาะปลั๊กอินที่เลือก, stop แล้ว listener/active backend = 0, events หลัง stop ไม่เข้า plugin, service registry ไม่มี leak

A3 Orca parser — fixture shape ทั้งเก่า/ปัจจุบัน parse ถูก, discovery มีทั้ง displayName/agents, malformed JSON/ok:false ให้ error ที่อธิบายได้, timeout/nonzero exit ไม่เข้าใจเป็นความสำเร็จ

A4 Sidebar contract — manifest version/capabilities/panel entry ถูกต้อง; ไม่มี terminal write, exec, filesystem access, fetch เป้าหมายใหม่; ห้ามเรียก `--approve`

A5 Status semantics — `done`=idle, working/blocked/waiting ถูก label, outcome success/failure จากทางการเท่านั้น, exit text ในหัวเรื่องไม่เปลี่ยนผล, no outcome ต้องยอมรับว่า unreported

A6 Language — mapping ครบใน spec, ข้าม dependency/generated/binary, percent รวม 100, stable sorting, empty project/permission error ให้ error หรือผลที่ชัดเจนตาม spec

A7 Packaging/skill — installation ใหม่เพียงพอ ไม่พึ่ง dist หรือ node_modules จากโฟลเดอร์เก่า, local skill copy มี origin ถูกต้องและมีเพียงคำแนะนำ workflow ไม่มี secret

## 7. UAT ทำเองบนเครื่องของคุณ

### UAT-1 Read-only discovery
- เปิด Orca เลือก Fleetline ใน folder repository ของตัวเอง
- `doctor --live` → reachable=true

- `discover --live` → repo/status/current workspace/worktree ps ไม่ล้มเหลว, ชื่อและ agent ตรงกับ Orca
- ยังไม่สร้าง worktree หรือ agent ใหม่

### UAT-2 Launch ผ่าน Fleetline
- `plan --backend codex --action launch ...` โดยไม่ `--execute` → เห็น argv และ dryRun=true เท่านั้น; Orca ไม่เปิด tab
- ตั้งใจรัน execute นัดหมายเฉพาะข้อความ read-only เช่นให้ Codex อ่าน portable skill
- ตรวจ launch receipt: outcome kind, handle/receipt/prompt และ worktreeId ตรงกับ Orca
- Codex report ว่าอ่าน `.agents/skills/orchestrate-with-orca/SKILL.md` และไม่แก้ไฟล์/เปิด agent อื่น
- ตรวจ ls/git status ว่าไม่มีไฟล์เพิ่ม

### UAT-3 Toggle ใน Orca
- ติดตั้ง plugin จาก `integrations/orca-sidebar` เป็น development folder
- เปิดจาก Orca Settings → Plugins โดยไม่ส่ง `--approve`
- ตรวจ consent มีเพียงสองสิทธิ์
- เปิด Fleetline panel → workspace/terminal/Codex rows ตรงกับหน้าจอ
- ปิด plugin → panel หาย, reopen → ข้อมูลยังอ่านได้
- ถ้า Orca ขอ re-consent หรือ fingerprint ใหม่ ต้องหยุดและตรวจว่าทำไม เปลี่ยนเฉพาะความสามารถ/สิทธิ์เมื่อตั้งใจ

### UAT-4 Status clarity
- สร้าง read-only test ที่มีหนึ่ง command failure และหนึ่ง success
- จบงานทั้งสองต้อง idle ไม่ใช่ success เสมอ
- official failure → command failed; success → command succeeded
- หาก API ไม่ให้ exit code/outcome → command outcome not reported
- Panel ไม่สามารถอ้างรายละเอียด exit 1 หากเห็นแค่ title ใน terminal

### UAT-5 Language
- รันในโฟลเดอร์ทดสอบของคุณ ไม่ใช่ root หรือ home
- ผลสัดส่วนรวม 100% ประเภทไฟล์ตรงกับสิ่งที่เห็นใน Git
- `node_modules`, `dist`, image/binary ไม่เปลี่ยนผล
- ไฟล์ไม่ถูกเขียนหรือลบ
- No Orca language sidebar expected for this release

### UAT-6 Safety/regression
- ไม่มี API key ใน log/source/skill
- ไม่พิมพ์เข้า terminal อัตโนมัติ, ไม่มี loop อ่าน TTY
- ให้เฉพาะ `--execute` เปิดงานจาก Fleetline
- ปิด/เริ่ม profile หลายรอบ ไม่มี listener สะสม ไม่มี agent ซ้ำ
- ปิด Orca แล้ว CLI แจ้ง error หรือ unreachable ชัดเจน ไม่ตีความเป็น success

## 8. Definition of done ของการส่ง Local
- ลิงก์ commit รุ่น Local + OS/Node/Orca/Codex versions
- ผล automated test แยกจาก UAT manual และว่าผ่านตัวไหนเอง
- screenshot panel กรณี idle/failure/success/unknown outcome
- git diff/status และไฟล์ที่เปลี่ยนแปลง
- known limitations ชัดเจน ต้องไม่ดัดแปลง permission ผ่าน terminal หรือรวม workarounds ที่ไม่ประกาศ
- หากเพิ่ม skill/docs ระบุ source path กับ install path ให้เหมือนกัน
