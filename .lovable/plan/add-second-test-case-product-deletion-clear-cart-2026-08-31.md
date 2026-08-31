# Add second test case: Product Deletion (Clear Cart)

Wire the uploaded add-on into the runner and the dashboard so users can pick
between two test cases.

## 1. runner/runner.py

Paste the "ADD TO runner.py" block verbatim, immediately above the
`TEST_CASES` dict (currently at line 1228), after the `LOGIN_LOGOUT` list:

- Constants: `CART_SWIPE_X1/Y1 = 843, 837`, `CART_SWIPE_X2/Y2 = 304, 827`,
  `CART_DELETE_SELECTOR` (instance 17), `CART_BACK_SELECTOR` (instance 0),
  `CART_EXTRA_BACK_SELECTOR` (instance 2) — copied unchanged.
- Helpers: `swipe_w3c_touch`, `get_first_category_row`,
  `delete_all_options_in_category`, `cart_back_to_cart`, `clear_entire_cart`.
- Step: `step_clear_cart`.
- `PRODUCT_DELETION` list — 15 steps, reusing the existing hardened
  `step_open_app` … `step_cart_tab`, then `step_clear_cart`, `step_save`,
  `step_signature`, `step_submit`, `step_wait_order`, `step_logout`.

Then extend the registry:

```python
TEST_CASES: dict[str, list[Callable[[Any], None]]] = {
    "login_logout": LOGIN_LOGOUT,
    "product_deletion": PRODUCT_DELETION,
}
```

No changes to existing login/QR steps or to `LOGIN_LOGOUT`.

## 2. src/lib/qserve-config.ts

Add the `product_deletion` entry to `TEST_CASES` with the exact 15 step
labels, in the same order as the Python step list, so the live progress and
results pages label each step correctly.

## Verification after wiring

- The dashboard test-case dropdown lists both "Login → Logout" (21 steps)
  and "Product Deletion (Clear Cart)" (15 steps); `listTestCases` derives the
  count from the labels array.
- A `product_deletion` run stores `steps_total = 15` and its 15 step names,
  so the results page renders the correct step table.
- Build check for TypeScript/route errors.

## Notes carried over from the add-on file

- The swipe coordinates and `instance(17)` selector are absolute and were
  captured on one device resolution; they may not hold on every device in the
  list (especially the Tab S9). Copied verbatim as requested.
- The test assumes the cart already has items; `clear_entire_cart` raises
  "No category rows found — cart was already empty" when nothing is present,
  so an empty cart shows as a clear FAIL rather than a silent pass.
