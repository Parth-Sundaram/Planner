// Right-click "Add to Planner": remembers what you clicked on, then opens the popup to confirm.
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "add", title: "Add to Planner", contexts: ["page", "selection", "link"] });
});
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  await chrome.storage.session.set({
    pending: { text: (info.selectionText || "").trim(), link: info.linkUrl || info.pageUrl || tab?.url || "", title: tab?.title || "" },
  });
  try { await chrome.action.openPopup(); } catch { /* older Chrome: the user clicks the toolbar icon */ }
});
