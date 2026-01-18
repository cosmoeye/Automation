import { chromium } from 'playwright';
import * as readline from 'readline';

// 사용자 입력을 받는 함수
function askQuestion(query: string): Promise<string> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise(resolve => rl.question(query, ans => {
    rl.close();
    resolve(ans);
  }));
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

async function main() {
  console.log('인천공항 주차 예약 조회 시작...\n');

  // 터미널 선택
  const terminalInput = await askQuestion('터미널을 선택하세요 (1: 제1터미널, 2: 제2터미널): ');
  const terminal = terminalInput.trim() === '2' ? 2 : 1;
  const terminalName = `제${terminal}터미널 예약주차장`;
  console.log(`\n선택된 터미널: ${terminalName}`);

  // 사용자로부터 날짜 입력받기
  const startDateInput = await askQuestion('\n예약 입차일자를 입력하세요 (YYYY-MM-DD): ');
  const endDateInput = await askQuestion('예약 출차일자를 입력하세요 (YYYY-MM-DD): ');

  console.log(`\n입차일: ${startDateInput}`);
  console.log(`출차일: ${endDateInput}`);

  // 시간 입력받기
  console.log('\n시간 범위를 지정하면 검색 시간을 크게 단축할 수 있습니다.');
  console.log('입력 형식: 단일 시간(09:00) 또는 범위(09:00~12:00, 09:00 ~ 12:00)');
  console.log('(전체 시간대를 확인하려면 Enter를 누르세요)\n');

  const startTimeInput = await askQuestion('입차 시간 범위 (예: 10:00~12:00): ');
  const endTimeInput = await askQuestion('출차 시간 범위 (예: 18:00~20:00): ');

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

      // 예약 가능한 조합 저장
      interface AvailableSlot {
        startDateTime: string;
        endDateTime: string;
        parkingFee: string;
        status: string;
      }
      const availableSlots: AvailableSlot[] = [];

      let totalChecked = 0;
      let availableCount = 0;

      // 1단계: 모든 시간대 조합 확인
      console.log('1단계: 전체 시간대 조합 스캔 중...\n');

      for (const startTime of startTimeSlots) {
        for (const endTime of endTimeSlots) {
          // 출차 시간이 입차 시간보다 늦어야 함
          const startDateTime = `${startDateInput} ${startTime}`;
          const endDateTime = `${endDateInput} ${endTime}`;

          // 날짜가 같은 경우 출차 시간이 입차 시간보다 늦어야 함
          if (startDateInput === endDateInput) {
            const [startHour, startMin] = startTime.split(':').map(Number);
            const [endHour, endMin] = endTime.split(':').map(Number);
            if (endHour * 60 + endMin <= startHour * 60 + startMin) {
              continue;
            }
          }

          totalChecked++;

          // 네트워크 요청 완료 대기를 위한 Promise 설정
          const responsePromise = page.waitForResponse(
            response => response.url().includes('/reserve/') && response.status() === 200,
            { timeout: 10000 }
          ).catch(() => null); // timeout 에러 무시

          // 입차일 설정
          await page.evaluate((dateTime) => {
            const input = document.querySelector('input[name="pinRsvDtm"]') as HTMLInputElement;
            if (input) {
              input.value = dateTime;
              const event = new Event('change', { bubbles: true });
              input.dispatchEvent(event);
            }
          }, startDateTime);

          // 짧은 대기 후 출차일 설정
          await page.waitForTimeout(100);

          // 출차일 설정
          await page.evaluate((dateTime) => {
            const input = document.querySelector('input[name="poutRsvDtm"]') as HTMLInputElement;
            if (input) {
              input.value = dateTime;
              const event = new Event('change', { bubbles: true });
              input.dispatchEvent(event);
            }
          }, endDateTime);

          // AJAX 완료 대기 (네트워크 요청 완료 또는 최대 2초)
          await Promise.race([
            responsePromise,
            page.waitForTimeout(2000)
          ]);

          // 결과 렌더링 대기
          await page.waitForTimeout(300);

          // 예약 가능 여부 확인
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

          // 예약 가능한 경우 저장
          if (resvePosblYn === 'Y') {
            availableCount++;
            availableSlots.push({
              startDateTime,
              endDateTime,
              parkingFee,
              status: resvePosblSttus
            });
          }

          // 진행 상황 표시 (10개마다)
          if (totalChecked % 10 === 0) {
            process.stdout.write(`\r확인 중... ${totalChecked}개 조합 확인 완료 (예약 가능: ${availableCount}개)`);
          }
        }
      }

      console.log(`\n\n총 ${totalChecked}개 조합 확인 완료\n`);

      // 결과 출력
      if (availableSlots.length > 0) {
        console.log('='.repeat(80));
        console.log(`✅ 예약 가능한 시간대 총 ${availableSlots.length}개 발견!`);
        console.log('='.repeat(80));
        console.log();

        // 결과 상세 출력 여부 확인
        const showDetail = await askQuestion(`\n전체 ${availableSlots.length}개 결과를 모두 출력하시겠습니까? (y/n, 기본값 n): `);

        if (showDetail.toLowerCase() === 'y') {
          console.log();
          availableSlots.forEach((slot, index) => {
            console.log(`[${index + 1}]`);
            console.log(`  입차: ${slot.startDateTime}`);
            console.log(`  출차: ${slot.endDateTime}`);
            console.log(`  요금: ${slot.parkingFee}원`);
            console.log(`  상태: ${slot.status}`);
            console.log();
          });
        } else {
          // 처음 5개만 출력
          console.log(`\n처음 5개 결과만 표시합니다:\n`);
          availableSlots.slice(0, 5).forEach((slot, index) => {
            console.log(`[${index + 1}]`);
            console.log(`  입차: ${slot.startDateTime}`);
            console.log(`  출차: ${slot.endDateTime}`);
            console.log(`  요금: ${slot.parkingFee}원`);
            console.log(`  상태: ${slot.status}`);
            console.log();
          });
          if (availableSlots.length > 5) {
            console.log(`... 외 ${availableSlots.length - 5}개\n`);
          }
        }

      } else {
        console.log('='.repeat(80));
        console.log('❌ 예약 불가능');
        console.log('='.repeat(80));
        console.log('\n해당 날짜에 예약 가능한 시간대가 없습니다.');
        console.log('다른 날짜를 시도해보세요.\n');
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
