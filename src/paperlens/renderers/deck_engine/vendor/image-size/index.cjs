"use strict";

const unsupported = () => {
  throw new Error("PptxGenJS image-size helper is disabled because it is unused by SourceLens");
};

module.exports = unsupported;
module.exports.imageSize = unsupported;
