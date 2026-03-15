import { chromium } from 'playwright';
import * as fs from 'fs';

// Playwright subprocess가 stdin을 상속해 readline이 닫히는 문제를 피하기 위해
// /dev/tty를 fs.readSync로 동기 블로킹 읽기 (이벤트 루프 차단이 의도적)
function askQuestion(query: string): Promise<string> {
  process.stdout.write(query);
  return new Promise((resolve) => {
    try {
      const fd = fs.openSync('/dev/tty', 'r');
      const chunks: number[] = [];
      const buf = Buffer.alloc(1);
      while (true) {
        const n = fs.readSync(fd, buf, 0, 1, null);
        if (n === 0 || buf[0] === 0x0a) break; // 0x0a = '\n'
        if (buf[0] !== 0x0d) chunks.push(buf[0]); // 0x0d = '\r' 무시
      }
      fs.closeSync(fd);
      resolve(Buffer.from(chunks).toString().trim());
    } catch {
      process.stdout.write('\n');
      resolve('');
    }
  });
}

// 시간대 배열 생성 (30분 단위)
function generateTimeSlots(startHour: number, endHour: number): string[] {
  const slots: string[] = [];
  for (let hour = startHour; hour <= endHour; hour++) {
    for (let minute of [0, 30]) {
      if (hour === endHour && minute === 30) break; // 마지막 시간은 00분까지만
      const timeStr = `${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}`;
      slots.push(timeStr);
    }
  }
  return slots;
}

// 시간 문자열을 분 단위로 변환 (비교용)
function timeToMinutes(time: string): number {
  const [hour, min] = time.split(':').map(Number);
  return hour * 60 + min;
}

// 시간 범위 파싱 (예: "10:00 ~ 12:00" 또는 "10:00~12:00" 또는 "10:00")
function parseTimeRange(input: string): { start: string; end: string } | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // 범위 형식인지 확인 (~ 또는 - 로 구분)
  const rangeMatch = trimmed.match(/^(\d{1,2}:\d{2})\s*[~\-]\s*(\d{1,2}:\d{2})$/);
  if (rangeMatch) {
    const start = rangeMatch[1].padStart(5, '0'); // "9:00" -> "09:00"
    const end = rangeMatch[2].padStart(5, '0');
    return { start, end };
  }

  // 단일 시간 형식인지 확인
  const singleMatch = trimmed.match(/^(\d{1,2}:\d{2})$/);
  if (singleMatch) {
    const time = singleMatch[1].padStart(5, '0');
    return { start: time, end: time };
  }

  return null;
}

// 시간 범위 내의 시간대만 필터링
function filterTimeSlotsByRange(slots: string[], range: { start: string; end: string }): string[] {
  const startMinutes = timeToMinutes(range.start);
  const endMinutes = timeToMinutes(range.end);

  return slots.filter(slot => {
    const slotMinutes = timeToMinutes(slot);
    return slotMinutes >= startMinutes && slotMinutes <= endMinutes;
  });
}

interface AvailableSlot {
  startDateTime: string;
  endDateTime: string;
  parkingFee: string;
  status: string;
}

async function openBookingBrowser(slot: AvailableSlot, terminalName: string) {
  console.log(`\n브라우저 창을 여는 중... (입차: ${slot.startDateTime}, 출차: ${slot.endDateTime})`);

  const bookingBrowser = await chromium.launch({ headless: false });
  const bookingContext = await bookingBrowser.newContext({ viewport: { width: 1280, height: 800 } });
  const bookingPage = await bookingContext.newPage();

  await bookingPage.goto('https://parking.airport.kr/reserve', { waitUntil: 'networkidle', timeout: 30000 });
  await bookingPage.waitForTimeout(2000);

  const link = bookingPage.locator('a', { hasText: terminalName });
  if (await link.count() > 0) {
    await link.click();
    await bookingPage.waitForTimeout(2000);

    await bookingPage.evaluate((dt) => {
      const input = document.querySelector('input[name="pinRsvDtm"]') as HTMLInputElement;
      if (input) { input.value = dt; input.dispatchEvent(new Event('change', { bubbles: true })); }
    }, slot.startDateTime);

    await bookingPage.waitForTimeout(300);

    await bookingPage.evaluate((dt) => {
      const input = document.querySelector('input[name="poutRsvDtm"]') as HTMLInputElement;
      if (input) { input.value = dt; input.dispatchEvent(new Event('change', { bubbles: true })); }
    }, slot.endDateTime);

    await bookingPage.waitForTimeout(1000);
  }

  console.log('✅ 브라우저가 열렸습니다. 예약을 진행해주세요.');
  console.log('(창을 닫으면 프로그램이 종료됩니다.)');
  await new Promise<void>(resolve => bookingBrowser.on('disconnected', () => resolve()));
}

async function main() {
  console.log('인천공항 주차 예약 조회 시작...\n');

  // CLI 인수 파싱 (pnpm dev -- <terminal> <start-date> <end-date> <start-time> <end-time> [--json])
  const rawArgs = process.argv.slice(2).filter(a => a !== '--');
  const jsonMode = rawArgs.includes('--json');
  const args = rawArgs.filter(a => a !== '--json');

  let terminalInput: string;
  let startDateInput: string;
  let endDateInput: string;
  let startTimeInput: string;
  let endTimeInput: string;

  if (args.length >= 3) {
    // CLI 인수로 전달된 경우
    terminalInput = args[0];
    startDateInput = args[1];
    endDateInput = args[2];
    startTimeInput = args[3] ?? '';
    endTimeInput = args[4] ?? '';
  } else {
    // 대화형 입력
    terminalInput = await askQuestion('터미널을 선택하세요 (1: 제1터미널, 2: 제2터미널): ');
    startDateInput = await askQuestion('\n예약 입차일자를 입력하세요 (YYYY-MM-DD): ');
    endDateInput = await askQuestion('예약 출차일자를 입력하세요 (YYYY-MM-DD): ');
    console.log('\n시간 범위를 지정하면 검색 시간을 크게 단축할 수 있습니다.');
    console.log('입력 형식: 단일 시간(09:00) 또는 범위(09:00~12:00, 09:00 ~ 12:00)');
    console.log('(전체 시간대를 확인하려면 Enter를 누르세요)\n');
    startTimeInput = await askQuestion('입차 시간 범위 (예: 10:00~12:00): ');
    endTimeInput = await askQuestion('출차 시간 범위 (예: 18:00~20:00): ');
  }

  // 터미널 선택
  const terminal = terminalInput.trim() === '2' ? 2 : 1;
  const terminalName = `제${terminal}터미널 예약주차장`;
  console.log(`선택된 터미널: ${terminalName}`);

  console.log('\n시간대별 예약 가능 여부를 확인합니다...\n');

  // 브라우저 실행 (headless 모드로 더 빠르게 실행)
  const browser = await chromium.launch({
    headless: true, // headless 모드로 실행 (브라우저 창 없이 백그라운드에서 실행)
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }
  });
  const page = await context.newPage();

  try {
    // 메인 페이지로 이동
    await page.goto('https://parking.airport.kr/reserve', {
      waitUntil: 'networkidle',
      timeout: 30000
    });

    console.log('메인 페이지 로드 완료:', page.url());

    // 페이지 로딩 대기
    await page.waitForTimeout(2000);

    console.log(`${terminalName} 링크 클릭 시도...`);

    // 링크를 찾아서 클릭
    const link = page.locator('a', { hasText: terminalName });
    const linkExists = await link.count() > 0;

    if (linkExists) {
      await link.click();
      await page.waitForTimeout(2000);

      // 시간대 생성 (00:00 ~ 23:30, 30분 단위) - 전체 시간대 지원
      const allTimeSlots = generateTimeSlots(0, 23);
      let startTimeSlots = [...allTimeSlots];
      let endTimeSlots = [...allTimeSlots];

      // 시간 범위 필터링
      const startTimeRange = parseTimeRange(startTimeInput);
      const endTimeRange = parseTimeRange(endTimeInput);

      if (startTimeRange) {
        startTimeSlots = filterTimeSlotsByRange(allTimeSlots, startTimeRange);
        if (startTimeRange.start === startTimeRange.end) {
          console.log(`입차 시간: ${startTimeRange.start}`);
        } else {
          console.log(`입차 시간 범위: ${startTimeRange.start} ~ ${startTimeRange.end}`);
        }
      }
      if (endTimeRange) {
        endTimeSlots = filterTimeSlotsByRange(allTimeSlots, endTimeRange);
        if (endTimeRange.start === endTimeRange.end) {
          console.log(`출차 시간: ${endTimeRange.start}`);
        } else {
          console.log(`출차 시간 범위: ${endTimeRange.start} ~ ${endTimeRange.end}`);
        }
      }

      console.log(`\n확인할 입차 시간대: ${startTimeSlots.length}개`);
      console.log(`확인할 출차 시간대: ${endTimeSlots.length}개`);
      console.log(`총 조합 수: ${startTimeSlots.length * endTimeSlots.length}개\n`);

      const allAvailableSlots: AvailableSlot[] = [];
      let totalChecked = 0;
      let availableCount = 0;
      let bookingRequested = false;

      const BATCH_SIZE = 10;

      if (jsonMode) {
        console.log(`JSON 모드: 전체 스캔 후 output.json 저장\n`);
      } else {
        console.log(`10개씩 스캔하며 결과를 실시간으로 출력합니다...\n`);
      }
      console.log('-'.repeat(80));

      async function askAfterBatch(label: string): Promise<boolean> {
        console.log(`\n--- ${label} (예약 가능: ${availableCount}개) ---`);
        if (allAvailableSlots.length > 0) {
          console.log('\n현재까지 발견된 예약 가능 슬롯:');
          allAvailableSlots.forEach((s, i) => {
            console.log(`  [${i + 1}] 입차: ${s.startDateTime}  출차: ${s.endDateTime}  요금: ${s.parkingFee || '-'}원  상태: ${s.status.trim()}`);
          });
          const answer = await askQuestion(`\n예약할 슬롯 번호를 입력하세요 (계속 스캔하려면 Enter): `);
          const slotNum = parseInt(answer.trim());
          if (!isNaN(slotNum) && slotNum >= 1 && slotNum <= allAvailableSlots.length) {
            await browser.close();
            bookingRequested = true;
            await openBookingBrowser(allAvailableSlots[slotNum - 1], terminalName);
            return true;
          }
        } else {
          const answer = await askQuestion(`예약 가능한 슬롯이 없습니다. 계속 스캔하시겠습니까? (Enter=계속, q=종료): `);
          if (answer.trim().toLowerCase() === 'q') {
            return true;
          }
        }
        console.log();
        return false;
      }

      outer: for (const startTime of startTimeSlots) {
        for (const endTime of endTimeSlots) {
          const startDateTime = `${startDateInput} ${startTime}`;
          const endDateTime = `${endDateInput} ${endTime}`;

          if (startDateInput === endDateInput) {
            const [startHour, startMin] = startTime.split(':').map(Number);
            const [endHour, endMin] = endTime.split(':').map(Number);
            if (endHour * 60 + endMin <= startHour * 60 + startMin) {
              continue;
            }
          }

          totalChecked++;

          const responsePromise = page.waitForResponse(
            response => response.url().includes('/reserve/') && response.status() === 200,
            { timeout: 10000 }
          ).catch(() => null);

          await page.evaluate((dateTime) => {
            const input = document.querySelector('input[name="pinRsvDtm"]') as HTMLInputElement;
            if (input) { input.value = dateTime; input.dispatchEvent(new Event('change', { bubbles: true })); }
          }, startDateTime);

          await page.waitForTimeout(100);

          await page.evaluate((dateTime) => {
            const input = document.querySelector('input[name="poutRsvDtm"]') as HTMLInputElement;
            if (input) { input.value = dateTime; input.dispatchEvent(new Event('change', { bubbles: true })); }
          }, endDateTime);

          await Promise.race([responsePromise, page.waitForTimeout(2000)]);
          await page.waitForTimeout(300);

          const resvePosblYn = await page.evaluate(() => {
            const input = document.querySelector('input[name="resvePosblYn"]') as HTMLInputElement;
            return input ? input.value : '';
          });

          const resvePosblSttus = await page.evaluate(() => {
            const input = document.querySelector('input[name="resvePosblSttus"]') as HTMLInputElement;
            return input ? input.value : '';
          });

          const parkingFee = await page.evaluate(() => {
            const input = document.querySelector('input[name="parkingFeeText"]') as HTMLInputElement;
            return input ? input.value : '';
          });

          if (resvePosblYn === 'Y') {
            availableCount++;
            allAvailableSlots.push({ startDateTime, endDateTime, parkingFee, status: resvePosblSttus });
            console.log(`✅ [${availableCount}] 입차: ${startDateTime}  출차: ${endDateTime}  요금: ${parkingFee || '-'}원  상태: ${resvePosblSttus.trim()}`);
          }

          if (jsonMode && totalChecked >= BATCH_SIZE) break outer;

          if (!jsonMode && totalChecked % BATCH_SIZE === 0) {
            const done = await askAfterBatch(`${totalChecked}개 완료`);
            if (done) break outer;
          }
        }
      }

      if (jsonMode) {
        // JSON 모드: 결과를 output.json에 저장
        const output = {
          timestamp: new Date().toISOString(),
          inputs: {
            terminal: terminalInput.trim(),
            terminalName,
            checkin_date: startDateInput,
            checkout_date: endDateInput,
            checkin_time: startTimeInput || '',
            checkout_time: endTimeInput || '',
          },
          stats: {
            total_checked: totalChecked,
            available_count: availableCount,
          },
          available_slots: allAvailableSlots,
        };
        fs.writeFileSync('output.json', JSON.stringify(output, null, 2), 'utf-8');
        console.log(`\n✅ output.json 저장 완료 (${availableCount}개 예약 가능 슬롯)`);
      } else if (!bookingRequested) {
        // 대화형 모드: 마지막 배치 잔여분 처리
        if (totalChecked % BATCH_SIZE !== 0) {
          const done = await askAfterBatch(`스캔 완료 (총 ${totalChecked}개)`);
          if (done) return;
        }

        console.log('\n' + '='.repeat(80));
        if (availableCount > 0) {
          console.log(`✅ 스캔 완료: 총 ${totalChecked}개 조합 중 ${availableCount}개 예약 가능`);
        } else {
          console.log(`❌ 스캔 완료: 총 ${totalChecked}개 조합 확인 — 예약 가능한 시간대 없음`);
          console.log('다른 날짜를 시도해보세요.');
        }
        console.log('='.repeat(80));
      }

    } else {
      console.log('링크를 찾을 수 없습니다.');
    }

  } catch (error) {
    console.error('\n오류 발생:', error);
  } finally {
    await browser.close();
    console.log('\n프로그램을 종료합니다.');
  }
}

main();
