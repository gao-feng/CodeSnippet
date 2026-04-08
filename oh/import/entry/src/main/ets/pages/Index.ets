import lazy { MMKV } from '@tencent/mmkv';
import fs from '@ohos.file.fs';
import promptAction from '@ohos.promptAction';
import common from '@ohos.app.ability.common';
import dlopenModule from 'libdlopen.so';
interface SmapsSnapshot {
  rssKb: number;
  pssKb: number;
  privateDirtyKb: number;
  swapPssKb: number;
  rawText: string;
}

@Entry
@Component
struct Index {
  @State private clickCount: number = 0;
  @State private resultText: string = 'Tap the button to write and read data with MMKV.';
  private mmkvInitialized: boolean = false;
  private context: common.Context = getContext(this);

  aboutToAppear(): void {
  }

  private extractValue(rawText: string, key: string): number {
    const pattern = new RegExp(`${key}:\\s+(\\d+)\\s+kB`);
    const match = rawText.match(pattern);
    return match && match[1] ? Number(match[1]) : -1;
  }

  private readSmapsRollup(): SmapsSnapshot | null {
    const filePath = '/proc/self/smaps_rollup';

    try {
      const file = fs.openSync(filePath, fs.OpenMode.READ_ONLY);
      const chunks: string[] = [];
      const bufferSize = 4096;

      while (true) {
        const buffer = new ArrayBuffer(bufferSize);
        const bytesRead = fs.readSync(file.fd, buffer);
        if (bytesRead <= 0) {
          break;
        }

        const bytes = new Uint8Array(buffer, 0, bytesRead);
        let chunk = '';
        for (let index = 0; index < bytes.length; index++) {
          chunk += String.fromCharCode(bytes[index]);
        }
        chunks.push(chunk);
      }

      fs.closeSync(file);
      const rawText = chunks.join('');
      if (!rawText) {
        console.warn(`[smaps_rollup] ${filePath} was read successfully but returned an empty string`);
      }

      return {
        rssKb: this.extractValue(rawText, 'Rss'),
        pssKb: this.extractValue(rawText, 'Pss'),
        privateDirtyKb: this.extractValue(rawText, 'Private_Dirty'),
        swapPssKb: this.extractValue(rawText, 'SwapPss'),
        rawText
      } as SmapsSnapshot;
    } catch (error) {
      console.error(`[smaps_rollup] failed to read ${filePath}: ${JSON.stringify(error)}`);
      return null;
    }
  }

  private formatSnapshot(tag: string, snapshot: SmapsSnapshot): string {
    return `[smaps_rollup][${tag}] rss=${snapshot.rssKb}kB, pss=${snapshot.pssKb}kB, privateDirty=${snapshot.privateDirtyKb}kB, swapPss=${snapshot.swapPssKb}kB`;
  }

  private formatDiff(before: SmapsSnapshot, after: SmapsSnapshot): string {
    return `[smaps_rollup][delta] rss=${after.rssKb - before.rssKb}kB, pss=${after.pssKb - before.pssKb}kB, privateDirty=${after.privateDirtyKb - before.privateDirtyKb}kB, swapPss=${after.swapPssKb - before.swapPssKb}kB`;
  }

  private dlopenMmkv(): string {
    const targetPath = '/data/storage/el1/bundle/libs/arm64/libmmkv.so';
    try {
      const result: string = dlopenModule.dlopenTarget(targetPath);
      console.info(`[dlopen] path=${targetPath}, result=${result}`);
      return result;
    } catch (error) {
      const message = `[dlopen] path=${targetPath}, error=${JSON.stringify(error)}`;
      console.error(message);
      return message;
    }
  }

  private callMmkv(): void {
    this.clickCount += 1;
    const beforeSnapshot = this.readSmapsRollup();

    if (!this.mmkvInitialized) {
      MMKV.initialize(this.context.getApplicationContext());
      this.mmkvInitialized = true;
    }

    // const mmkv = MMKV.defaultMMKV();
    // const value = `Call #${this.clickCount}, timestamp=${Date.now()}`;
    //
    // mmkv.encodeString('demo_message', value);
    // const savedValue = mmkv.decodeString('demo_message') ?? 'MMKV read failed';
    if (beforeSnapshot) {
      console.info(this.formatSnapshot('before_mmkv', beforeSnapshot));
      console.info(`[smaps_rollup][before_raw]\n${beforeSnapshot.rawText}`);
    }
    const afterSnapshot = this.readSmapsRollup();
    if (afterSnapshot) {
      console.info(this.formatSnapshot('after_mmkv', afterSnapshot));
      console.info(`[smaps_rollup][after_raw]\n${afterSnapshot.rawText}`);
    }
    if (beforeSnapshot && afterSnapshot) {
      console.info(this.formatDiff(beforeSnapshot, afterSnapshot));
    }

    promptAction.showToast({
      message: 'MMKV read/write finished'
    });
  }

  build() {
    Column({ space: 20 }) {
      Text('HarmonyOS MMKV Demo')
        .fontSize(28)
        .fontWeight(FontWeight.Bold)
        .fontColor('#222222')

      Text('The home page has one button that triggers libmmkv write and read.')
        .fontSize(16)
        .fontColor('#666666')
        .textAlign(TextAlign.Center)

      Button('Call libmmkv')
        .width('72%')
        .height(48)
        .fontSize(18)
        .fontWeight(FontWeight.Medium)
        .backgroundColor('#1677FF')
        .borderRadius(24)
        .onClick(() => {
          // this.callMmkv();
          this.dlopenMmkv();
        })

      Text(this.resultText)
        .width('100%')
        .fontSize(16)
        .fontColor('#333333')
        .textAlign(TextAlign.Center)
        .padding({ left: 20, right: 20 })
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
    .padding({ left: 24, right: 24 })
    .backgroundColor('#F7F9FC')
  }
}
