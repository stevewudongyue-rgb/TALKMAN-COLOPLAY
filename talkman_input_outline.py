"""输入区域高亮：不截屏、不激活、不接收鼠标，坐标来自已核验控件。"""
import sys


class InputOutline:
    def __init__(self):
        self.native = None

    def show(self, target, region):
        try:
            if self.native is None:
                if sys.platform == 'win32':
                    from talkman_coloplay_highlight import WindowsOutline
                    self.native = WindowsOutline()
                elif sys.platform == 'darwin':
                    self.native = MacInputOutline()
                else:
                    return False
            return self.native.draw_region(target, region.rect, region.number)
        except Exception:
            self.hide()
            return False

    def hide(self):
        if self.native:
            self.native.hide()

    def close(self):
        if self.native:
            self.native.close()
            self.native = None


class MacInputOutline:
    def __init__(self):
        import AppKit as A
        self.A = A
        self.panels = []
        for _ in range(5):
            panel = A.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                A.NSMakeRect(0,0,1,1), A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
                A.NSBackingStoreBuffered, False)
            panel.setReleasedWhenClosed_(False)
            panel.setIgnoresMouseEvents_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setLevel_(A.NSFloatingWindowLevel)
            panel.setBackgroundColor_(A.NSColor.colorWithSRGBRed_green_blue_alpha_(.36,1,.55,1))
            self.panels.append(panel)
        self.label = A.NSTextField.labelWithString_('')
        self.label.setTextColor_(A.NSColor.blackColor())
        self.label.setFont_(A.NSFont.boldSystemFontOfSize_(16))
        self.label.setFrame_(A.NSMakeRect(4,2,28,24))
        self.panels[-1].contentView().addSubview_(self.label)

    def draw_region(self, target, rect, number):
        A = self.A
        import os
        front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None or int(front.processIdentifier()) not in (target.pid, os.getpid()):
            self.hide()
            return False
        x,y,w,h = rect
        top = A.NSScreen.screens()[0].frame().size.height
        edges = [(x,y,w,3),(x,y+h-3,w,3),(x,y,3,h),(x+w-3,y,3,h),(x,y,32,26)]
        self.label.setStringValue_(str(number))
        for panel,(left,upper,width,height) in zip(self.panels,edges):
            panel.setFrame_display_(A.NSMakeRect(left,top-upper-height,width,height),True)
            panel.orderFrontRegardless()
        return True

    def hide(self):
        for panel in self.panels:panel.orderOut_(None)

    def close(self):
        for panel in self.panels:panel.close()
        self.panels=[]
