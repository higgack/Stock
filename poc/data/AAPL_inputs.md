# Apple Inc. (AAPL) - Manual Inputs for DCF / Comps PoC

Source: Apple FY2024 10-K (fiscal year ended Sep 28, 2024). Public figures.
Use these as inputs when running `/dcf AAPL` or `/comps AAPL` so the agent
doesn't need live data connectors.

## Identity
- Ticker: AAPL
- Exchange: NASDAQ
- Fiscal year end: Last Saturday of September
- Sector: Technology / Consumer Electronics

## Income Statement (USD millions, FY)
| Metric              | FY2022  | FY2023  | FY2024  |
|---------------------|---------|---------|---------|
| Total revenue       | 394,328 | 383,285 | 391,035 |
|   Products          | 316,199 | 298,085 | 294,866 |
|   Services          |  78,129 |  85,200 |  96,169 |
| Cost of sales       | 223,546 | 214,137 | 210,352 |
| Gross profit        | 170,782 | 169,148 | 180,683 |
| Operating expenses  |  51,345 |  54,847 |  57,467 |
|   R&D               |  26,251 |  29,915 |  31,370 |
|   SG&A              |  25,094 |  24,932 |  26,097 |
| Operating income    | 119,437 | 114,301 | 123,216 |
| Net income          |  99,803 |  96,995 |  93,736 |
| Diluted EPS (USD)   |   6.11  |   6.13  |   6.08  |

## Balance Sheet selected (USD millions, end of FY2024)
| Item                          | Value   |
|-------------------------------|---------|
| Cash & marketable securities  | 65,171  |
| Total assets                  | 364,980 |
| Total debt (ST + LT)          | 106,629 |
| Total equity                  |  56,950 |

## Cash Flow selected (USD millions, FY2024)
| Item                  | Value   |
|-----------------------|---------|
| CFO                   | 118,254 |
| CapEx                 |  (9,447)|
| Free cash flow        | 108,807 |
| Share repurchases     | (94,949)|
| Dividends paid        | (15,234)|

## Market data (illustrative — replace with current quote when running)
- Diluted shares outstanding: ~15,408 million
- 10Y US Treasury yield: ~4.3%
- Equity risk premium: 5.0%
- Levered beta (5Y monthly): ~1.25
- Pre-tax cost of debt: ~4.5%
- Effective tax rate: ~24%

## Suggested peer group for `/comps`
- Microsoft (MSFT) - software platform with consumer overlap
- Alphabet (GOOGL) - mega-cap tech, ad-driven services
- Meta Platforms (META) - mega-cap tech, ads + devices
- Samsung Electronics (005930.KS) - hardware peer (different accounting, optional)
- Sony Group (SONY) - consumer electronics + services

Note: Pure peer set is small for AAPL because of scale. Agent will likely
warn that peer multiples don't translate cleanly.

## Suggested DCF assumptions to start
- Projection horizon: 5 years (FY2025E - FY2029E)
- Revenue CAGR base case: 4-5% (Services +10%, Products +1-2%)
- Terminal growth: 2.5%
- Terminal EBITDA margin: ~33% (in line with FY2024)
- WACC base case: ~9% (CAPM with above inputs gives ~9.0-9.5%)
- Sensitivity: WACC 7-11% × terminal g 1.5-3.5%
