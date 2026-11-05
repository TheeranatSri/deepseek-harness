# Harness → Claude Code / Codex

ตัวเชื่อมให้ DeepSeek Harness เป็น coordinator และสั่ง Claude Code กับ Codex CLI ที่ติดตั้งบนเครื่อง ผ่าน MCP ที่ Harness รองรับอยู่แล้ว ไม่ต้องแก้ agent loop หรือ fork Harness

นี่เป็นตัวเชื่อมเพิ่มเติมที่สร้างสำหรับงานนี้ ไม่ใช่ plugin ทางการของ DeepSeek และไม่ได้ติดตั้งหรือทดสอบกับบัญชีส่วนตัวของคุณจากที่นี่

## ข้อจำกัดที่เปลี่ยน

| เรื่อง | ตัวเชื่อมนี้ |
|---|---|
| ส่งงานได้ครั้งเดียว | ใช้ชื่อ worker เดิมเพื่อ resume native session ID เดิมได้หลาย turn |
| context ของแต่ละตัวแยกกัน | coordinator เขียน shared context; ทุกงานรับ snapshot เดียวกัน ณ เวลาส่งงาน |
| เวอร์ชัน CLI ถูกผูกกับ plugin | เรียก CLI ที่ติดตั้งบนเครื่อง หรือ path ที่กำหนดใน config |
| จำกัดจำนวนงานพร้อมกัน | `max_parallel = 0` ไม่ตั้งเพดานใน bridge; ตั้งจำนวนบวกได้ |
| งานยาวติด MCP timeout | ส่งเป็น background job แล้ว poll สั้น ๆ; `task_timeout_seconds = 0` ไม่ตั้ง deadline ของงาน |
| มองเห็นเฉพาะคำตอบท้ายสุด | อ่าน native JSONL events, tool/command activity และ raw logs ได้ |
| เปลี่ยนโมเดลแต่ละงานไม่ได้ | ระบุ `model` ใน `worker_submit` หรือใช้ค่าเดิมจาก native CLI |
| ต้องขออนุญาตระหว่างงาน | เลือก `unrestricted` เพื่อส่ง native bypass flags ทั้งสอง CLI |
| output ยาวถูกตัด | เก็บคำตอบและ logs เต็ม อ่านผ่าน pagination ที่มี cursor ชัดเจน |

Bridge ไม่เพิ่ม `--max-turns`, `--max-budget-usd` หรือ token budget ของตนเอง โควตาบัญชี, rate limits, ขนาด context ของโมเดล, policy ขององค์กร, ข้อจำกัดใน native CLI และทรัพยากรเครื่องยังมีผลตามเดิม

## ติดตั้งบน macOS / Linux

ต้องมี Python 3.11+, Node.js 24.9+, npm, และ Claude Code/Codex CLI ที่ติดตั้งและล็อกอินแล้ว ตรวจ CLI ด้วย `claude --version` และ `codex --version`

หลัง clone fork ให้เปิด terminal ที่ root ของ repository:

```bash
cd integrations/cli-workers
bash install.sh /absolute/path/to/your/project unrestricted
bash local/launch.sh
```

แทน path ด้วยโฟลเดอร์งานจริง ถ้ามีช่องว่างให้ใส่ double quotes

`unrestricted` ใช้ `bypassPermissions` ของ Claude และ `--dangerously-bypass-approvals-and-sandbox` ของ Codex จึงให้คำสั่งที่โมเดลสร้างรันด้วยสิทธิ์ผู้ใช้บนเครื่อง เลือกใช้กับ project หรือเครื่องแยกที่คุณตั้งใจให้สิทธิ์นี้

ถ้าต้องการคง native workspace permissions ให้ใช้:

```bash
bash install.sh /absolute/path/to/your/project workspace
```

`workspace` ส่ง `acceptEdits` ให้ Claude และ `workspace-write` พร้อม `approval_policy="never"` ให้ Codex คำสั่งที่ native policy ไม่อนุญาตจะถูกปฏิเสธ ไม่เปิดหน้าถามค้างไว้ โหมดนี้ไม่ได้เพิ่ม OS sandbox ให้ Claude

Installer สร้าง virtual environment, ติดตั้ง Python package ด้วย dependency versions ใน `requirements.lock`, และสร้าง `local/workers.toml`, `local/workers.cordis.yml`, `local/launch.sh` โดยไม่คัดลอก token ลงไฟล์ รองรับ CLI paths ที่มีช่องว่าง นอกจากนี้ยังติดตั้ง Harness ลง `local/runtime` ด้วย npm ที่ปิด workspace resolution

Launcher เรียก `local/runtime/node_modules/@deepseek-ai/dsh/lib/bin.js` ผ่าน Node โดยตรง ไม่ต้องมีคำสั่ง `dsh` ใน PATH และไม่พึ่งการเลือก executable ของ `npx` ภายใน checkout ตัว runtime ใช้ `@deepseek-ai/dsh@0.2.2-rc.1` และเปิด Web UI ที่พอร์ตมาตรฐาน 3080 ถ้ามี Harness ใช้อยู่แล้ว ให้ใช้ overlay ที่สร้างไว้กับคำสั่งเปิด Harness ของคุณ:

```bash
dsh web --patch /absolute/path/to/dsh-cli-workers/local/workers.cordis.yml
```

ตั้งค่า model/credentials ของ coordinator ใน Harness ตามปกติ ส่วน credentials ของ worker ใช้ native login หรือ environment ของแต่ละ CLI; โควตาของทั้งสามส่วนไม่ได้รวมเป็นบัญชีเดียว

ถ้า browser ไม่เปิดเอง ให้เปิด launch URL ที่แสดงใน Terminal เพื่อรับ session cookie การเปิด URL พอร์ตเปล่าโดยยังไม่มี cookie จะถูกปฏิเสธ

ถ้าเคยติดตั้งเวอร์ชันเดิมแล้วพบ `sh: dsh: command not found` ให้ pull โค้ดล่าสุด แล้วรัน `install.sh` ด้วย workspace และ policy เดิมอีกครั้ง Installer จะอัปเดต Python package ติดตั้ง runtime และสร้าง `local/launch.sh` ใหม่ State และ native session เดิมยังอยู่

## ใช้งานจาก Harness

เมื่อเชื่อมสำเร็จ เครื่องมือจะขึ้นต้นด้วย `mcp__cli_workers__` เช่น `mcp__cli_workers__worker_submit`

สำหรับ workflow นี้ ให้ coordinator ใช้เครื่องมือชุดนี้ แทน `subagent_codex` / `subagent_claude_code` ซึ่งเป็นตัวเชื่อมแบบงานเดียวของ Harness คุณปิด official subagent plugins สองรายการนั้นได้ถ้าไม่ต้องการให้ coordinator เลือกผิดชุด

ตัวอย่างคำสั่งส่งให้ coordinator:

> ใช้ cli_workers ทำงานนี้ เริ่มด้วย shared_context_set เก็บโจทย์และข้อกำหนด ส่งให้ worker ชื่อ planner backend claude วางแผน รอผลด้วย worker_wait และอ่าน worker_result ให้ครบ จากนั้นสรุปผลที่ต้องส่งต่อไว้ใน shared_context_set แล้วให้ worker ชื่อ coder backend codex ลงมือทำ หากต้องแก้ไข ส่ง worker_submit ไปที่ชื่อ coder เดิมเพื่อให้ใช้ session เดิม ตรวจผลก่อนรายงานว่าสำเร็จ

ตัวอย่าง tool arguments:

```json
{"worker":"planner","backend":"claude","prompt":"ตรวจ project และเสนอแผน implementation"}
```

```json
{"worker":"coder","backend":"codex","prompt":"ลงมือตามแผนใน shared context"}
```

```json
{"worker":"coder","prompt":"ต่อจากงานเดิม แก้กรณี missing values และรัน validation"}
```

`worker_submit` คืน job ID ทันที ใช้ `worker_wait` จนเป็น `completed`, `failed`, `cancelled` หรือ `interrupted` แล้วอ่าน `worker_result` โดยตาม `next_offset` จนเป็น `null` คำตอบที่ยังไม่จบอาจมี partial text แต่ห้ามใช้แทนผลสำเร็จ

Shared context เป็น facts/handoffs ที่ coordinator บันทึกอย่างชัดเจน ไม่ใช่การรวม private chat history ของ Claude กับ Codex อัตโนมัติ แต่ละ worker จำบทสนทนาของตัวเองผ่าน native resume

## เครื่องมือ

| Tool | ใช้ทำอะไร |
|---|---|
| `worker_submit` | สร้าง worker หรือส่ง turn ต่อ; ส่งหลาย turn เข้า worker เดียวได้ โดยทำตามลำดับ FIFO |
| `worker_list` | ดู session ID, งานที่รอ และค่าจำกัดที่ operator ตั้งไว้ |
| `worker_status` / `worker_wait` | ดูสถานะ หรือรอผลไม่เกิน 20 วินาทีต่อ poll; ไม่ใช่ deadline ของงาน |
| `worker_result` | อ่านคำตอบพร้อม status/usage; pagination ไม่มีการตัดข้อมูลที่เก็บ |
| `worker_events` | อ่าน native JSONL ตาม `after_sequence` และ `count` |
| `worker_log` | อ่าน stdout/stderr แบบ byte pagination เมื่อต้องวิเคราะห์ปัญหา |
| `worker_cancel` | ยกเลิก job นั้น; ส่ง TERM แล้ว KILL ให้ native process group เมื่อจำเป็น |
| `worker_reset` | ให้ worker ว่างเริ่ม session ใหม่ โดยเก็บผลเก่าไว้ |
| `worker_recover` | ปลดสถานะ interrupted หลังตรวจ native processes และ workspace แล้ว |
| `shared_context_get` / `shared_context_set` | อ่าน/เขียน facts กลาง; รองรับ revision check |

## State และการหยุดงาน

State อยู่ที่ `~/.local/share/dsh-cli-workers/<workspace-hash>/` เป็น SQLite และ logs โฟลเดอร์จำกัดสิทธิ์ให้เจ้าของ; logs อาจมีเนื้อหา project ดิบ Native transcripts และ credentials อยู่ในที่เก็บตามปกติของแต่ละ CLI

CLI เปิด process ใหม่ทุก turn แต่ resume session ID เดิม จึงต่อบริบทได้หลังปิดและเปิด Harness ใหม่ ไม่ใช่การ attach terminal session ที่เปิดค้างไว้

งานใน worker เดียวทำทีละ turn ส่วนคนละ worker รันพร้อมกันได้ ถ้าแก้ไฟล์เดียวกันควรแยก git worktree/project ก่อน ตัวเชื่อมไม่ merge ไฟล์ให้เอง

เมื่อหยุด server ตามปกติ งานที่ยังรันจะถูกยกเลิก และผลที่เสร็จแล้วยังอยู่ ถ้า server ถูก kill อย่างฉับพลัน งานค้างถูกทำเครื่องหมาย `interrupted` และไม่ replay อัตโนมัติ ตรวจ process ของ CLI และการเปลี่ยนแปลงไฟล์ก่อนใช้ `worker_recover` การ cancel ไม่ย้อนคืน side effects และโปรแกรมที่ daemonize ออกจาก process group อาจต้องหยุดแยกเอง

เมื่อ turn หนึ่งล้มเหลว turn ที่ส่งเข้าคิวต่อจากมันจะไม่ถูกรันโดยอัตโนมัติ ต้องประเมินผลก่อนส่งงานใหม่ ตัวเชื่อมไม่ retry งานเมื่อชน quota เพื่อหลีกเลี่ยงการทำ side effects ซ้ำ

หนึ่ง state directory รับ server ได้เพียง process เดียว ป้องกันการ resume native session เดียวพร้อมกันจากหลาย Host

## Config

```toml
workspace = "/absolute/path/to/project"
state_dir = "/absolute/path/to/private/state"
codex_command = "codex"
claude_command = "claude"
permission_policy = "unrestricted"
max_parallel = 0
task_timeout_seconds = 0
terminate_grace_seconds = 3
```

เปลี่ยน config แล้ว restart Harness ไม่มีการเปลี่ยน permission policy จาก model-facing tool ค่าศูนย์ของสอง field ข้างต้นหมายถึงไม่มีเพดานเพิ่มจาก bridge

Overlay ส่ง environment ที่ระบุผ่าน DSH's credential scrubbing โดยอ้างค่าจาก environment ขณะรัน ไม่ฝัง token ไว้ใน YAML ถ้าใช้ cloud-provider authentication หรือ environment พิเศษ ให้เพิ่มชื่อที่ต้องใช้ใน `env` expression ของ overlay เอง

## ทดสอบ

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m pytest -q
```

Tests ใช้ subprocess fixtures และ official MCP client เพื่อทดสอบ resume, FIFO, shared context, concurrency, cancellation, crash recovery, large outputs และการเชื่อม MCP โดยไม่เรียก model API ดูผลตรวจเวอร์ชันและข้อจำกัดการตรวจจริงใน `VALIDATION.md`

อ้างอิง API: [Harness MCP client](../../packages/mcp/mcp-client/README.md), [Codex noninteractive](https://learn.chatgpt.com/docs/non-interactive-mode), [Claude CLI](https://code.claude.com/docs/en/cli-reference), [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
