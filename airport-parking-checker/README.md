# 인천공항 예약 주차 조회 프로그램

인천공항 예약 주차 사이트에서 특정 날짜의 예약 가능한 입출차 시간 조합을 조회합니다.

## 설치 방법

```bash
# 프로젝트를 원하는 경로로 이동
mv airport-parking-checker /Users/wonseok/workspace/nodejs/

# 해당 디렉토리로 이동
cd /Users/wonseok/workspace/nodejs/airport-parking-checker

# 패키지 설치
npm install
```

## 실행 방법

```bash
# 개발 모드 실행
npm run dev

# 빌드 후 실행
npm run build
npm start
```

## 프로젝트 구조

```
airport-parking-checker/
├── src/
│   └── index.ts          # 메인 실행 파일
├── package.json
├── tsconfig.json
└── README.md
```

## 다음 단계

1. 실제 페이지 구조 분석
2. 날짜 입력 자동화
3. 예약 가능 시간 추출 로직 작성
