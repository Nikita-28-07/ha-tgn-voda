# TGN Voda (Home Assistant)

Интеграция для ЛК МУП «Управление «Водоканал» (Таганрог): сенсоры баланса и сервисы для передачи и истории показаний.

**Репозиторий:** https://github.com/nikita2807/ha-tgn-voda

## Возможности
- Сенсоры: `to_pay_now`, `accrued_in_period`, `paid_amount`
- Сервис `tgn_voda.submit_readings` — отправка показаний
- Сервис `tgn_voda.get_history` — загрузка истории (событие `tgn_voda_history`)
- Настраиваемая проверка TLS (certifi/кастомный CA/отключение в отладке)

## Установка через HACS
1. Открой HACS → Integrations → ⋮ → **Custom repositories**
2. Вставь URL репозитория: `https://github.com/nikita2807/ha-tgn-voda` и выбери категорию **Integration**
3. Нажми **Add**, затем установи интеграцию **TGN Voda** и перезапусти Home Assistant

## Конфигурация (YAML)
```yaml
tgn_voda:
  login: !secret tgn_login
  password: !secret tgn_password
  account_id: "800000000"
  verify_ssl: true            # true | false | "/config/ca.pem"
  scan_interval: 1800         # сек
```

## Использование сервисов
Передача показаний:
```yaml
service: tgn_voda.submit_readings
data:
  readings:
    "123456": 600
```

История:
```yaml
service: tgn_voda.get_history
data:
  date_from: "01.07.2025"
  date_to: "31.08.2025"
```