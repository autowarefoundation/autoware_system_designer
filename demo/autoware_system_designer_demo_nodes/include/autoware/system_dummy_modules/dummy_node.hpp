// Copyright 2026 TIER IV, inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef AUTOWARE__SYSTEM_DUMMY_MODULES__DUMMY_NODE_HPP_
#define AUTOWARE__SYSTEM_DUMMY_MODULES__DUMMY_NODE_HPP_

#include <rclcpp/rclcpp.hpp>

#include <std_msgs/msg/color_rgba.hpp>
#include <std_msgs/msg/header.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace autoware::system_dummy_modules
{

namespace detail
{
template <typename T>
struct IsVector : std::false_type
{
};
template <typename T, typename A>
struct IsVector<std::vector<T, A>> : std::true_type
{
};

template <typename T>
T number(const uint64_t count)
{
  if constexpr (std::is_same_v<T, bool>) {
    return count % 2 == 1;
  } else if constexpr (std::is_floating_point_v<T>) {
    return static_cast<T>(count % 1000) * static_cast<T>(0.1);
  } else {
    return static_cast<T>(count % 100);
  }
}

inline void fill(std_msgs::msg::Header & message, const std::string & label, const uint64_t, rclcpp::Node & node, int)
{
  message.stamp = node.now();
  message.frame_id = label;
}

inline void fill(std_msgs::msg::ColorRGBA & message, const std::string &, const uint64_t count, rclcpp::Node &, int)
{
  message.r = count % 3 == 0 ? 1.0f : 0.0f;
  message.g = count % 3 == 1 ? 1.0f : 0.0f;
  message.b = count % 3 == 2 ? 1.0f : 0.0f;
  message.a = 1.0f;
}

// Messages with a `data` field: text, a number, or four numbers.
template <typename MessageT>
auto fill(MessageT & message, const std::string & label, const uint64_t count, rclcpp::Node &, int)
  -> decltype(message.data, void())
{
  using DataT = std::decay_t<decltype(message.data)>;
  if constexpr (std::is_same_v<DataT, std::string>) {
    message.data = label + " #" + std::to_string(count);
  } else if constexpr (IsVector<DataT>::value) {
    for (uint64_t i = 0; i < 4; ++i) {
      message.data.push_back(number<typename DataT::value_type>(count + i));
    }
  } else {
    message.data = number<DataT>(count);
  }
}

// Any other message is published default-constructed.
template <typename MessageT>
void fill(MessageT &, const std::string &, const uint64_t, rclcpp::Node &, long)
{
}
}  // namespace detail

/// Quality of service of a port; a subscriber without a design value accepts any publisher.
inline rclcpp::QoS port_qos(const std::string & reliability, const std::string & durability, const bool subscriber)
{
  rclcpp::QoS qos(10);
  if (reliability == "best_effort" || (reliability.empty() && subscriber)) {
    qos.best_effort();
  }
  if (durability == "transient_local") {
    qos.transient_local();
  }
  return qos;
}

/// Node of a dummy node design: every port carries generated messages, and each process publishes
/// its outputs when its trigger conditions fire.
class DummyNode : public rclcpp::Node
{
public:
  /// One way a process is fired; `gates` lists groups of inputs of which one each must have arrived.
  struct Rule
  {
    std::string process;
    std::vector<std::vector<std::string>> gates;
    bool once{false};
    bool fired{false};
  };

  DummyNode(const std::string & name, const rclcpp::NodeOptions & options)
  : rclcpp::Node(
      name, rclcpp::NodeOptions(options)
              .allow_undeclared_parameters(true)
              .automatically_declare_parameters_from_overrides(true))
  {
  }

protected:
  template <typename MessageT>
  void input(const std::string & name, const rclcpp::QoS & qos)
  {
    subscriptions_.push_back(
      create_subscription<MessageT>(
        "~/input/" + name, qos, [this, name](const typename MessageT::ConstSharedPtr message) {
          Port & port = inputs_[name];
          ++port.count;
          if (due(port)) {
            RCLCPP_INFO(
              get_logger(), "heard %s #%lu: %s", name.c_str(), port.count, to_yaml(*message, true).c_str());
          }
          for (Rule & rule : input_rules_[name]) {
            fire(rule);
          }
        }));
    inputs_[name];
  }

  template <typename MessageT>
  void output(const std::string & name, const rclcpp::QoS & qos)
  {
    auto publisher = create_publisher<MessageT>("~/output/" + name, qos);
    outputs_[name] = [this, name, publisher]() {
      Port & port = output_ports_[name];
      ++port.count;
      MessageT message;
      detail::fill(message, std::string(get_name()) + " " + name, port.count, *this, 0);
      publisher->publish(message);
      if (due(port)) {
        RCLCPP_INFO(get_logger(), "said %s #%lu: %s", name.c_str(), port.count, to_yaml(message, true).c_str());
      }
    };
  }

  template <typename ServiceT>
  void server(const std::string & name)
  {
    services_.push_back(
      create_service<ServiceT>(
        "~/service/" + name, [this, name](
                               const typename ServiceT::Request::SharedPtr,
                               typename ServiceT::Response::SharedPtr) {
          RCLCPP_INFO(get_logger(), "served %s", name.c_str());
        }));
  }

  /// Process publishing `outputs` and then running the processes in `triggers`.
  void process(const std::string & name, std::vector<std::string> outputs, std::vector<std::string> triggers)
  {
    processes_[name] = Process{std::move(outputs), std::move(triggers), false};
  }

  void on_timer(const double rate_hz, Rule rule)
  {
    const auto period = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::duration<double>(1.0 / rate_hz));
    const auto shared = std::make_shared<Rule>(std::move(rule));
    timers_.push_back(create_wall_timer(period, [this, shared]() { fire(*shared); }));
  }

  void on_input(const std::string & name, Rule rule) { input_rules_[name].push_back(std::move(rule)); }

  void on_start(Rule rule)
  {
    rule.once = true;
    const auto shared = std::make_shared<Rule>(std::move(rule));
    timers_.push_back(create_wall_timer(std::chrono::milliseconds(100), [this, shared]() { fire(*shared); }));
  }

private:
  struct Port
  {
    uint64_t count{0};
    std::chrono::steady_clock::time_point logged{};
  };

  struct Process
  {
    std::vector<std::string> outputs;
    std::vector<std::string> triggers;
    bool running{false};
  };

  // One log line per port every five seconds.
  static bool due(Port & port)
  {
    const auto now = std::chrono::steady_clock::now();
    if (port.count > 1 && now - port.logged < std::chrono::seconds(5)) {
      return false;
    }
    port.logged = now;
    return true;
  }

  void fire(Rule & rule)
  {
    if (rule.once && rule.fired) {
      return;
    }
    for (const auto & group : rule.gates) {
      bool arrived = false;
      for (const auto & name : group) {
        arrived = arrived || inputs_[name].count > 0;
      }
      if (!arrived) {
        return;
      }
    }
    rule.fired = true;
    run(rule.process);
  }

  void run(const std::string & name)
  {
    Process & process = processes_[name];
    if (process.running) {
      return;
    }
    process.running = true;
    for (const auto & output : process.outputs) {
      outputs_[output]();
    }
    for (const auto & next : process.triggers) {
      run(next);
    }
    process.running = false;
  }

  std::map<std::string, Port> inputs_;
  std::map<std::string, Port> output_ports_;
  std::map<std::string, std::function<void()>> outputs_;
  std::map<std::string, Process> processes_;
  std::map<std::string, std::vector<Rule>> input_rules_;
  std::vector<rclcpp::SubscriptionBase::SharedPtr> subscriptions_;
  std::vector<rclcpp::ServiceBase::SharedPtr> services_;
  std::vector<rclcpp::TimerBase::SharedPtr> timers_;
};

}  // namespace autoware::system_dummy_modules

#endif  // AUTOWARE__SYSTEM_DUMMY_MODULES__DUMMY_NODE_HPP_
