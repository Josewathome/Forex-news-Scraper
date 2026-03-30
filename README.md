follow this gemin link on josephgathithi@gmail.com : https://gemini.google.com/app/ff2a71f3025a5ef8


for the scraping tool in claude: in the tana email:  https://claude.ai/chat/846067e2-4b0c-49ba-86b1-fbe589f3f86d


API: GET:  http://localhost:8000/myfxbook/events?currency=CNY&start_date=2026-03-10&end_date=2026-03-11&currency=AUD
Response:
```json
{
    "start_date": "2026-03-10",
    "end_date": "2026-03-11",
    "timezone": "(GMT -12:00) International Date Line West",
    "events": [
        {
            "time": "03:30",
            "timezone": "(GMT -12:00) International Date Line West",
            "currency": "AUD",
            "impact": "low",
            "event": "Building Permits YoY\n            (Jan)",
            "actual": "-15.7%",
            "forecast": "-15.7%",
            "previous": "1.1%",
            "date": "2026-03-10"
        },
        {
            "time": "03:30",
            "timezone": "(GMT -12:00) International Date Line West",
            "currency": "AUD",
            "impact": "low",
            "event": "Building Permits MoM\n            (Jan)",
            "actual": "-7.2%",
            "forecast": "-7.2%",
            "previous": "-14.9%",
            "date": "2026-03-10"
        },
        {
            "time": "03:00",
            "timezone": "(GMT -12:00) International Date Line West",
            "currency": "CNY",
            "impact": "low",
            "event": "National People's Congress",
            "actual": null,
            "forecast": null,
            "previous": null,
            "date": "2026-03-11"
        },
        {
            "time": "13:30",
            "timezone": "(GMT -12:00) International Date Line West",
            "currency": "CNY",
            "impact": "low",
            "event": "Vehicle Sales YoY\n            (Feb)",
            "actual": "-15.4%",
            "forecast": null,
            "previous": "-3.2%",
            "date": "2026-03-11"
        }
    ],
    "source": "myfxbook",
    "cached": false
}
```

API: GET: http://localhost:8000/forexfactory/events?currency=USD&date=2026-03-30
RESPONSE:
```json
{
    "date": "2026-03-30",
    "timezone": "UTC",
    "currencies": [
        "USD"
    ],
    "events": [
        {
            "time": "5:30pm",
            "timezone": "UTC",
            "currency": "USD",
            "impact": "high",
            "event": "Fed Chair Powell Speaks",
            "actual": null,
            "forecast": null,
            "previous": null
        },
        {
            "time": "11:00pm",
            "timezone": "UTC",
            "currency": "USD",
            "impact": "low",
            "event": "FOMC Member Williams Speaks",
            "actual": null,
            "forecast": null,
            "previous": null
        }
    ],
    "source": "forexfactory",
    "cached": false
}
```