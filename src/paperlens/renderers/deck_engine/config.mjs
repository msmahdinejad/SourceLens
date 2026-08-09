export const CONFIG = {
  font: "Aptos",
  keepCredits: true,
};

export function configure(options = {}) {
  CONFIG.font = String(options.font_family || options.fontFamily || "Aptos").trim() || "Aptos";
  CONFIG.keepCredits = options.show_image_credits !== false && options.showImageCredits !== false;
}

export const W = 13.333;
export const H = 7.5;
