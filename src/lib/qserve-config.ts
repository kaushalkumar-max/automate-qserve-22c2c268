export type TestCase = { key: string; name: string; steps: string[] };
export type Device = { id: string; name: string; os_version: string };

export const TEST_CASES: Record<string, TestCase> = {
  login_logout: {
    key: "login_logout",
    name: "Login → Logout",
    steps: [
      "Open App","Tap Scan QR from Gallery","Photo Picker Opens","Select QR Image",
      "Tap Done in Picker","Return to App","Tap Login Button","Wait for Home Screen",
      "Open Catalogue Tab","Select Boys Brand","Open First Product","Fill Quantity Fields",
      "Tap Plus Button","Tap Add to Cart","Tap Home Button","Open Cart Tab","Tap SAVE",
      "Draw Signature","Submit Order","Wait for Order Save","Tap Logout",
    ],
  },
  product_deletion: {
    key: "product_deletion",
    name: "Product Deletion (Clear Cart)",
    steps: [
      "Open App", "Tap Scan QR from Gallery", "Photo Picker Opens",
      "Select QR Image", "Tap Done in Picker", "Return to App",
      "Tap Login Button", "Wait for Home Screen", "Open Cart Tab",
      "Clear All Cart Items", "Tap SAVE", "Draw Signature",
      "Submit Order", "Wait for Order Save", "Tap Logout",
    ],
  },
  search_functionality: {
    key: "search_functionality",
    name: "Search Functionality (Double Booking)",
    steps: [
      "Open App", "Tap Scan QR from Gallery", "Photo Picker Opens",
      "Select QR Image", "Tap Done in Picker", "Return to App",
      "Tap Login Button", "Wait for Home Screen",
      "Search Product (Home)", "Fill Sizes Round 1 (= 1)",
      "Tap Plus (Round 1)", "Add to Cart (Round 1)",
      "Tap Home Button", "Open Catalogue Tab", "Select Boys Brand",
      "Search Product (Boys)", "Fill Sizes Round 2 (= 2)",
      "Tap Plus (Round 2)", "Add to Cart (Round 2)", "Tap Home Button",
      "Open Cart Tab", "Tap SAVE", "Draw Signature", "Submit Order",
      "Smart Logout",
    ],
  },
  filter_functionality: {
    key: "filter_functionality",
    name: "Filter Functionality (Category + Sub-Category)",
    steps: [
      "Open App", "Tap Scan QR from Gallery", "Photo Picker Opens",
      "Select QR Image", "Tap Done in Picker", "Return to App",
      "Tap Login Button", "Wait for Home Screen",
      "Open Catalogue Tab", "Select Boys Brand",
      "Open Filter Panel", "Tick Category (Denim)",
      "Open Sub-Category Tab", "Tick Sub-Category (Gymindigo)",
      "Tap Apply Filters", "Open Filtered Result",
      "Fill Quantity Fields", "Tap Plus Button", "Tap Add to Cart",
      "Tap Home Button", "Open Cart Tab", "Tap SAVE", "Draw Signature",
      "Submit Order", "Smart Logout",
    ],
  },
};

export const DEVICES: Device[] = [
  { id: "galaxy-s23-13", name: "Samsung Galaxy S23", os_version: "13.0" },
  { id: "galaxy-s24-14", name: "Samsung Galaxy S24", os_version: "14.0" },
  { id: "galaxy-s22-12", name: "Samsung Galaxy S22", os_version: "12.0" },
  { id: "galaxy-s21-12", name: "Samsung Galaxy S21", os_version: "12.0" },
  { id: "pixel-8-14", name: "Google Pixel 8", os_version: "14.0" },
  { id: "pixel-7-13", name: "Google Pixel 7", os_version: "13.0" },
  { id: "pixel-6-12", name: "Google Pixel 6", os_version: "12.0" },
  { id: "oneplus-11r-13", name: "OnePlus 11R", os_version: "13.0" },
  { id: "xiaomi-redmi-note-11-11", name: "Xiaomi Redmi Note 11", os_version: "11.0" },
  { id: "galaxy-tab-s9-13", name: "Samsung Galaxy Tab S9", os_version: "13.0" },
];

export const APP_PACKAGE = "com.qart.qserve";
