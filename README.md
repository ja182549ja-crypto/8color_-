# 8color_-

โปรแกรมทดลองกับงานวิจัย

## โปรแกรมทดลองการมองสีด้วย Tobii Pro Spark

โปรแกรม `tobii_spark_color_attention.py` แสดงสิ่งเร้าเต็มหน้าจอ รับข้อมูล gaze จาก Tobii Pro SDK และบันทึกไฟล์ CSV

## ลำดับการทดลอง

1. กรอกชื่อผู้เข้าร่วมและเชื่อมต่อ eye tracker
2. พักสายตาบนพื้นสีเทา 5 วินาที
3. แสดงตัวเลขนับถอยหลัง 3, 2, 1
4. ทำการทดลอง 4 รอบ ในแต่ละรอบวงกลม 7 ลูกจะทยอยปรากฏห่างกัน 0.25 วินาที แล้วค้างครบทั้ง 7 ลูกอีก 10 วินาที
5. ตำแหน่งของสีและลำดับที่วงกลมปรากฏจะสุ่มใหม่ทุกครั้ง โดยแต่ละสีจะไม่อยู่ตำแหน่งเดิมติดกันสองรอบ

มีสีตามโจทย์ 6 สี และใช้วงกลมสีขาวเป็นวงกลมตัวควบคุมลำดับที่ 7 หากต้องการเปลี่ยนสีหรือลบ/เปลี่ยนวงกลมตัวควบคุม ให้แก้ `COLORS` ในไฟล์ Python

## สิ่งที่ต้องติดตั้ง

- Windows 10/11 และ Python 3.10 (เวอร์ชันที่คู่มือ SDK ปัจจุบันระบุ)
- Tobii Pro Eye Tracker Manager พร้อม driver ของ Tobii Pro Spark
- Tobii Pro SDK สำหรับ Python ซึ่งมีโมดูล `tobii_research`

ติดตั้ง SDK ใน Python environment ที่จะใช้รันโปรแกรม:

```powershell
python -m pip install tobii_research
```

ก่อนทดลอง ให้ตั้งค่า Display Setup และสอบเทียบผู้เข้าร่วมใน Tobii Pro Eye Tracker Manager บนจอเดียวกับที่จะใช้แสดงสิ่งเร้า จากนั้นเปิด PowerShell ในโฟลเดอร์นี้และรัน:

```powershell
python .\tobii_spark_color_attention.py
```

หน้าต่างจะเปิดเต็มจอ กด `Esc` เพื่อหยุดการทดลองและบันทึกข้อมูลที่เก็บได้ หรือปิดหน้าต่างก่อนเริ่ม

## เกณฑ์การสรุปผล

- โปรแกรมอ่านตำแหน่ง gaze ที่ normalize เป็น 0–1 จาก SDK แล้วจับคู่กับพิกัดหน้าจอของวงกลมที่ปรากฏอยู่
- gaze ที่อยู่ภายในขอบวงกลมเดียวกันต่อเนื่องอย่างน้อย 250 ms นับเป็น fixation ของสีนั้น
- หลังมี fixation แล้ว การมองออกจากวงกลมนั้นต่อเนื่องมากกว่า 100 ms บันทึกเป็น `focus_loss`; การออกไปไม่เกิน 100 ms บันทึกเป็น `brief_glance`
- gaze ที่ SDK ระบุว่าไม่ valid จะไม่นับเป็นการมองออกนอก AOI และจะตัดช่วงการต่อเนื่องของ fixation
- หนึ่งช่วงจะเริ่มใหม่เมื่อมีช่องว่างระหว่าง sample เกิน 100 ms เพื่อไม่ถือว่าข้อมูลที่ขาดเป็นการจ้องต่อเนื่อง

## ไฟล์ผลลัพธ์

ไฟล์จะถูกสร้างในโฟลเดอร์ `data` ข้างไฟล์โปรแกรม โดยใช้เวลาเริ่ม session ในชื่อไฟล์ ไม่ใช้ชื่อผู้เข้าร่วมเป็นชื่อไฟล์:

- `*_gaze.csv` — sample gaze ดิบ, validity, phase, AOI และเวลาจากเครื่องติดตามสายตา
- `*_summary.csv` — เวลาที่ gaze อยู่ในแต่ละสี, fixation ที่ผ่านเกณฑ์, glance และ focus loss แยกตามรอบ
- `*_events.csv` — เหตุการณ์ glance และ focus loss พร้อมระยะเวลา
- `*_stimuli.csv` — สี, ตำแหน่ง, ลำดับและเวลาที่วงกลมแต่ละลูกปรากฏ

## ทดลองด้วยเมาส์ (ไม่ใช้ Tobii)

ใช้โปรแกรมนี้เพื่อทดสอบหน้าจอ ลำดับสิ่งเร้า และการประมวลผลที่บ้าน โดย pointer ของเมาส์จำลองตำแหน่ง gaze:

โหมดนี้ใช้ Python ที่มี Tkinter/Tcl-Tk และไม่ต้องมี Tobii Spark หรือ Tobii Pro SDK

~~~powershell
python .\mouse_trial_gui.py
~~~

เพิ่ม --fast เพื่อย่อช่วงพัก/นับถอยหลัง/แสดงสิ่งเร้าสำหรับตรวจการทำงานของหน้าจอ:

~~~powershell
python .\mouse_trial_gui.py --fast
~~~

ไฟล์จะบันทึกในโฟลเดอร์ data โดยมี source_device=mouse_simulation และใช้รูปแบบ CSV เดียวกับโปรแกรม Tobii จึงนำไปสร้าง PNG และแยกไฟล์รายผู้เข้าร่วมด้วย analyze_gaze_results.py ได้ ข้อมูลจากเมาส์เป็นข้อมูลจำลอง ไม่ใช่ข้อมูล eye-tracking สำหรับใช้สรุปผลวิจัย

## สร้าง Gaze Path และแยกไฟล์รายผู้เข้าร่วม

ติดตั้ง Matplotlib เพิ่มใน environment เดียวกับ Tobii SDK แล้วรันหลังจบการทดลอง:

```powershell
python -m pip install -r .\requirements-analysis.txt
python .\analyze_gaze_results.py
```

ผลจะอยู่ใน `data/participants/participant_001/`, `participant_002/` ตามลำดับชื่อผู้เข้าร่วม แต่ละโฟลเดอร์มี `gaze_path.png` และไฟล์ gaze ดิบ, summary, events และ stimuli ของคนนั้น ส่วน `participant_index.csv` ใช้จับคู่รหัสกับชื่อผู้เข้าร่วม ไฟล์ข้อมูลและกราฟอยู่ใน `data/` ซึ่ง Git ไม่อัปโหลด

กราฟใช้พิกัดหน้าจอจากข้อมูล stimulus ถ้ามี หากเป็นข้อมูลเก่าที่ไม่มี resolution metadata โปรแกรมจะใช้ขนาดจอปัจจุบัน สามารถกำหนดเองได้ เช่น `python .\analyze_gaze_results.py --screen-width 1920 --screen-height 1080`

## ข้อควรทราบ

ไฟล์สรุปนี้เป็นการจัดกลุ่ม sample ตามเกณฑ์ที่ระบุไว้ในโปรแกรม ไม่ใช่การตรวจ fixation แบบผู้ผลิตหรือการวิเคราะห์ทางสถิติ ผลควรตรวจสอบกับการสอบเทียบ คุณภาพสัญญาณ และแผนวิเคราะห์ของงานวิจัยก่อนนำไปสรุปผล

เอกสารของ Tobii: [ติดตั้งและเริ่มใช้ Python SDK](https://developer.tobiipro.com/python/python-getting-started.html), [รายชื่อ eye tracker ที่รองรับ](https://developer.tobiipro.com/tobiiprosdk/eyetrackercompatibility.html), และ [คู่มือ Tobii Pro Spark](https://nbtltd.com/wp-content/uploads/2022/12/tobii_pro_spark_user_manual.pdf)
