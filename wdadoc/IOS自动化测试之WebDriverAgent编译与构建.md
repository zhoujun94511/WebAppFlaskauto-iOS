# iOS 自动化测试之 WebDriverAgent 编译与构建

> 3.1.11　WebDriverAgent 获取与编译

## 一、WebDriverAgent 简介

Facebook 出品：

- 实现了一个 server，通过它可以远程控制 iOS 设备：启动应用、关闭应用、点击、滚动等操作；
- 通过连接 `XCTest.framework` 调用苹果的 API 执行动作；
- 支持多个设备同时进行自动化；
- Appium、Macaca 均已集成。

但 WebDriverAgent 仅提供了一个 server（以及用于元素定位的 inspect），并没有像 Appium 那样提供 Java / Python 的 Client 端来编写脚本。脚本执行时由 Client 端向 server 发送指令再运行，因此你需要自行实现 Client 端——即用 Java / Python 的 WebDriver 库进行封装后发送指令。所以 WebDriverAgent 其实类似于 Appium server，本质上就是一个 server：它在 iOS 客户端实现了一个 WebDriver Server，借助这个 server 即可远程控制 iOS 设备进行测试。

## 二、关于开发者证书

Appium 与 iOS 手机通讯正是借助 WDA。在把 WDA 配置到 iOS 手机之前，需要通过 Xcode 将苹果开发者证书编译进 WDA 才会生效——**这一步是整个流程中报错概率最高的环节**。

苹果开发者证书分免费与收费两种，对自动化测试而言免费版即够用；只是通过 Xcode 构建、编译进 WDA 的免费证书只有 **7 天有效期**，到期后需重新构建一次。若能获得正式版开发者证书则可长期使用。

## 三、获取 WebDriverAgent

来源一：直接使用 Appium 集成的 WebDriverAgent，用 Xcode 编译证书后使用。

来源二：获取 WebDriverAgent 源码到本地——

```bash
git clone https://github.com/appium/WebDriverAgent.git
```

或者使用 Xcode 自带的 Clone 功能拉取源码到本地：

![在 Xcode 欢迎界面选择 Clone an existing project](./image001.png)

*在 Xcode 欢迎界面选择 “Clone an existing project”。*

![粘贴仓库地址并点击 Clone](./image002.png)

*粘贴仓库地址 `https://github.com/appium/WebDriverAgent.git`，点击右下角 “Clone”。*

![信任 github.com 仓库证书](./image003.png)

*若提示无法验证 github.com 的身份，点击 “Trust”。*

![选择 master 分支](./image004.png)

*选择要检出的分支（`master`），点击 “Clone”。*

![选择本地保存位置](./image005.png)

*选择本地保存位置后点击 “Clone” 完成拉取。*

## 四、选择开发者账号（Team）

打开工程后，选择自己免费申请的开发者账号——在 **Team** 处选择你的账号：

![在 Team 处选择开发者账号](./image006.png)

## 五、修改 BundleID

修改 WebDriverAgent 的 **Bundle Identifier**，在后面加上专属后缀，确保与他人不重复，例如改成 `com.facebook.WebDriverAgentRunner.webeyer`：

![修改 WebDriverAgentRunner 的 BundleID](./image007.png)

## 六、选择调试设备并运行

1. 选择调试设备：**Product → Destination → 你的 iPhone**；
2. 选择 Scheme：**Product → Scheme → WebDriverAgentRunner**；
3. 运行测试：**Product → Test**，手机上即会安装对应 app；
4. 进入手机 **设置 → 通用 → VPN 与设备管理 → 开发者应用**，点击 **信任**，然后再次 **Product → Test** 即可。

## 七、替换 Appium 下的 WebDriverAgent

删除 Appium 原有的 WebDriverAgent 文件夹，把编译好的 WebDriverAgent 放进去即可：

```text
# 使用 npm 安装时，目录在：
cd /usr/local/lib/node_modules/appium/node_modules/appium-xcuitest-driver/WebDriverAgent/

# 使用 Appium Desktop 安装时，目录在：
/Applications/Appium.app/Contents/Resources/app/node_modules/appium/node_modules/appium-xcuitest-driver/WebDriverAgent/
```

## 八、信任描述文件并重新构建

第一次构建时会报错（如下图），这是因为构建到手机上的 WebDriverAgent 需要信任其描述文件。手机设置路径：**设置 → 通用 → VPN 与设备管理 → 选择描述文件并信任**。

![首次构建因未信任描述文件而报错](./image008.png)

信任描述文件后，在 Xcode 再次执行 **Product → Test** 构建一次即可成功。手机里会出现一个 **“WebDriverAgentRunner-Runner”**，走到这一步就可以对 iOS 真机进行自动化测试了。

![手机出现 WebDriverAgentRunner-Runner](./image009.png)

## 九、环境验证 —— 端口转发

构建 WebDriverAgent 到手机成功后，Xcode 控制台会打印出手机的 IP 与端口：

![Xcode 控制台打印设备 IP 与端口](./image010.png)

在终端执行端口转发：

```bash
iproxy 8300 8100
```

该命令用于把手机的端口映射到电脑的某个端口：其中 `8100` 是手机端口，`8300` 是映射到 Mac 的端口。若映射失败，先安装依赖：

```bash
brew install usbmuxd
```

端口转发成功后，在 Safari（或浏览器）中访问 `本地地址 + 转发端口`：[http://localhost:8300/status](http://localhost:8300/status)。得到类似下面的输出，即表示 WebDriverAgent 服务器状态正常：

```json
{
  "value": {
    "message": "WebDriverAgent is ready to accept commands",
    "state": "success",
    "os": {
      "testmanagerdVersion": 28,
      "name": "iOS",
      "sdkVersion": "14.5",
      "version": "13.3"
    },
    "ios": {
      "ip": "192.168.3.18"
    },
    "ready": true,
    "build": {
      "time": "Aug  4 2021 10:13:40",
      "productBundleIdentifier": "com.facebook.WebDriverAgentRunner"
    }
  },
  "sessionId": null
}
```
