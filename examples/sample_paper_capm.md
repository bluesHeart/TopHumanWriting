
---

**Title: An Empirical Examination of the Capital Asset Pricing Model (CAPM) in the Context of Modern Equity Markets**

**Abstract**
This paper investigates the validity and predictive power of the Capital Asset Pricing Model (CAPM) in contemporary equity markets. Despite its foundational role in finance, the CAPM has faced persistent criticism regarding its assumptions and empirical performance. Using historical stock data from the S&P 500 (2010–2023), we test the linear relationship between expected return and systematic risk (beta). Our findings indicate that while CAPM provides a useful baseline for risk-return analysis, it fails to fully capture cross-sectional return variations, with anomalies such as size and value effects contributing to significant deviations. We conclude by discussing the implications for portfolio management and the relevance of multifactor models as extensions.

**Keywords:** CAPM, asset pricing, systematic risk, beta, equity markets, empirical finance.

**1. Introduction**
The Capital Asset Pricing Model, developed by Sharpe (1964), Lintner (1965), and Mossin (1966), remains a cornerstone of modern financial theory. It posits that the expected return on an asset is linearly related to its beta, a measure of systematic risk relative to the market portfolio. However, empirical challenges—such as the model’s reliance on unrealistic assumptions (e.g., perfect markets, homogeneous expectations) and the emergence of return anomalies—have spurred debates about its practical utility. This paper aims to reassess CAPM’s applicability using recent market data, contributing to the ongoing discourse on asset pricing models.

**2. Literature Review**
Early empirical studies by Black, Jensen, and Scholes (1972) and Fama and MacBeth (1973) provided initial support for CAPM. However, subsequent research uncovered limitations: the size effect (Banz, 1981), the value premium (Fama & French, 1992), and momentum (Jegadeesh & Titman, 1993). These findings led to the development of multifactor models, notably the Fama-French three-factor and five-factor models. Recent studies also highlight the impact of behavioral factors and market frictions. This review synthesizes key criticisms and extensions of CAPM, setting the stage for our empirical analysis.

**3. Theoretical Framework**
CAPM is derived from Markowitz’s portfolio theory and assumes investors are rational, risk-averse, and hold diversified portfolios. The model expresses expected return as:

\\[
E(R_i) = R_f + \\beta_i [E(R_m) - R_f]
\\]

where \\(E(R_i)\\) is the expected return on asset \\(i\\), \\(R_f\\) is the risk-free rate, \\(E(R_m)\\) is the expected market return, and \\(\\beta_i\\) is the asset’s sensitivity to market movements. Beta is calculated as:

\\[
\\beta_i = \\frac{\\text{Cov}(R_i, R_m)}{\\text{Var}(R_m)}.
\\]

Despite its elegance, CAPM’s assumptions are often violated in real markets, prompting questions about its empirical robustness.

**4. Data and Methodology**
We collect monthly returns for S&P 500 constituents from January 2010 to December 2023, using the S&P 500 index as the market proxy and 10-year U.S. Treasury yields as the risk-free rate. Portfolios are formed based on beta rankings, and time-series regressions are run to estimate beta for each portfolio. Cross-sectional tests follow the Fama-MacBeth two-step procedure to examine the risk-return relationship. Control variables include size (market capitalization) and book-to-market ratios.

**5. Empirical Results**
Our analysis reveals a positive but weak relationship between beta and average returns. High-beta portfolios did not consistently outperform low-beta portfolios, contradicting CAPM predictions. Factors like market capitalization and valuation ratios showed significant explanatory power. For instance, small-cap and high book-to-market stocks generated excess returns not captured by beta alone. These results align with prior evidence supporting multifactor models.

**6. Discussion**
The findings suggest that CAPM, while useful for conceptualizing risk, is insufficient for explaining actual returns in modern markets. Limitations include static beta estimates, overlooking non-systematic risk factors, and changing market conditions. Practitioners should consider integrating additional factors (e.g., value, momentum) into risk assessment and portfolio construction. Future research could explore conditional CAPM specifications or machine learning approaches to enhance predictive accuracy.

**7. Conclusion**
This paper demonstrates that CAPM’s simplicity comes at the cost of empirical precision. Although it remains a foundational tool in finance education and practice, its standalone application is inadequate for capturing the complexity of equity returns. Investors and analysts are encouraged to adopt multifactor frameworks that account for a broader set of risk dimensions. Further studies should investigate CAPM’s performance in different market regimes and geographic contexts.

**References**

- Fama, E. F., & French, K. R. (1992). The cross-section of expected stock returns. *Journal of Finance*, 47(2), 427–465.
- Sharpe, W. F. (1964). Capital asset prices: A theory of market equilibrium under conditions of risk. *Journal of Finance*, 19(3), 425–442.
- Lintner, J. (1965). The valuation of risk assets and the selection of risky investments in stock portfolios and capital budgets. *Review of Economics and Statistics*, 47(1), 13–37.
- Black, F., Jensen, M. C., & Scholes, M. (1972). The capital asset pricing model: Some empirical tests. In *Studies in the Theory of Capital Markets*. Praeger Publishers.
- Fama, E. F., & MacBeth, J. D. (1973). Risk, return, and equilibrium: Empirical tests. *Journal of Political Economy*, 81(3), 607–636.

---

**Note:** This is a structured outline for a full academic paper. To develop it further, you could expand the literature review, include more detailed econometric analysis (e.g., GARCH models for time-varying volatility), or compare CAPM with alternative models like the Arbitrage Pricing Theory (APT) or factor-based approaches.
